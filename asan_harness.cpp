// asan_harness.cpp
//
// ASAN/UBSAN/LSAN 原生内存安全 harness: 复刻 btree.py 的“页引用计数 +
// 毒化检疫 + 快照延迟回收”生命周期模型, 在多线程读写交错下验证。
//
// 关键设计(避免一切锁嵌套):
//   * 只用一把全局递归锁保护 PageStore 与 Tree 的所有状态; 线程在锁外
//     执行耗时逻辑, 锁仅护临界区。
//   * 每个版本是一棵有界小树(1 根 + 固定 fanout 叶), 结构不累积。
//   * unref 归零即 delete, ASAN 立即把该内存标记不可访问; 任何对已回收
//     pid 的 access 都会解引用毒化指针并被 AddressSanitizer 现场捕获。
//
// 编译(单线程, 默认):
//   clang++ -std=c++17 -O1 -g -fsanitize=address,undefined \
//           -fno-omit-frame-pointer asan_harness.cpp -o asan_harness
// 多线程压测(加 -DASAN_MULTITHREAD):
//   clang++ ... -DASAN_MULTITHREAD asan_harness.cpp -o asan_harness_mt
//
// 期望: 退出码 0, ASAN/UBSAN/LSAN 无任何报告。
#include <atomic>
#include <cassert>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <random>
#include <set>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

struct Page {
    int pid;
    std::atomic<int> refcnt{0};
    std::vector<int> keys;
    explicit Page(int id) : pid(id) { keys.reserve(4); }
};

class PageStore {
public:
    std::recursive_mutex mu;                 // 唯一全局锁, 递归容忍辅助调用
    std::unordered_map<int, Page*> live;
    std::unordered_map<int, Page*> quarantine_ptr;
    std::set<int> quarantine;
    int next_pid = 0;
    std::atomic<long> allocs{0}, frees{0}, uaf{0}, refcnt_bad{0};
    int peak_live = 0;

    Page* alloc_locked() {
        int pid = ++next_pid;
        Page* p = new Page(pid);
        live.emplace(pid, p);
        ++allocs;
        peak_live = std::max(peak_live, static_cast<int>(live.size()));
        return p;
    }

    // 调用方须已持锁。命中已回收页 => 故意解引用, 让 ASAN 现场报错。
    Page* access_locked(int pid) {
        auto it = live.find(pid);
        if (it == live.end()) {
            ++uaf;
            std::cerr << "UAF pid=" << pid << std::endl;
            void* bad = quarantine_ptr.count(pid)
                            ? static_cast<void*>(quarantine_ptr[pid])
                            : reinterpret_cast<void*>(0x8);
            volatile int sink = *static_cast<volatile int*>(bad);
            (void)sink;
            abort();
        }
        return it->second;
    }

    void ref_locked(int pid) {
        access_locked(pid)->refcnt.fetch_add(1, std::memory_order_relaxed);
    }

    void unref_locked(int pid) {
        Page* p = access_locked(pid);
        int after = p->refcnt.fetch_sub(1, std::memory_order_acq_rel) - 1;
        if (after < 0) {
            ++refcnt_bad;
            std::cerr << "negative refcnt pid=" << pid << std::endl;
            abort();
        }
        if (after == 0) {
            live.erase(pid);
            quarantine.insert(pid);
            quarantine_ptr[pid] = p;
            delete p;                         // ASAN 立即标记不可访问
            ++frees;
        }
    }
};

struct Snapshot {
    std::vector<int> pinned;
};

class Tree {
public:
    explicit Tree(PageStore* s) : store(s) {
        std::lock_guard<std::recursive_mutex> g(store->mu);
        root = store->alloc_locked()->pid;
        store->ref_locked(root);
        children[root] = {};
    }

    // 有界写事务: 新根 + 固定数量新叶替换整棵小树; 旧树释放引用, 被快照
    // 钉住的旧页因 refcnt>0 而延迟回收。
    void write(int fanout, std::mt19937& rng) {
        std::lock_guard<std::recursive_mutex> g(store->mu);
        std::vector<int> old_pinned = collect_locked(root);

        int new_root = store->alloc_locked()->pid;
        std::vector<int> new_kids;
        new_kids.reserve(fanout);
        for (int i = 0; i < fanout; ++i) {
            Page* leaf = store->alloc_locked();
            leaf->keys.push_back(static_cast<int>(rng() % 1000));
            new_kids.push_back(leaf->pid);
        }
        children[new_root] = new_kids;
        for (int pid : new_kids) store->ref_locked(pid);
        store->ref_locked(new_root);

        for (int pid : old_pinned) store->unref_locked(pid);
        root = new_root;
        prune_locked();
    }

    Snapshot snapshot_locked_held() {
        Snapshot s;
        s.pinned = collect_locked(root);
        for (int pid : s.pinned) store->ref_locked(pid);
        return s;
    }

    void release_locked(Snapshot& s) {
        for (int pid : s.pinned) store->unref_locked(pid);
        s.pinned.clear();
    }

    long iterate_locked(const Snapshot& s) {
        long sum = 0;
        for (int pid : s.pinned) {
            Page* p = store->access_locked(pid);   // 毒化守卫
            for (int k : p->keys) sum += k;
        }
        return sum;
    }

    std::vector<int> collect_locked(int r) {
        std::vector<int> out;
        std::vector<int> stk{r};
        while (!stk.empty()) {
            int pid = stk.back();
            stk.pop_back();
            store->access_locked(pid);
            out.push_back(pid);
            auto it = children.find(pid);
            if (it != children.end())
                for (int c : it->second) stk.push_back(c);
        }
        return out;
    }

    void prune_locked() {
        for (auto it = children.begin(); it != children.end();) {
            if (store->live.find(it->first) == store->live.end())
                it = children.erase(it);
            else
                ++it;
        }
    }

    PageStore* store;
    int root = 0;
    std::unordered_map<int, std::vector<int>> children;
};

}  // namespace

int main() {
    PageStore store;
    Tree tree(&store);
    std::atomic<bool> stop{false};
    std::atomic<long> iters{0};

    auto writer = [&](unsigned seed) {
        std::mt19937 rng(seed);
        while (!stop.load()) {
            tree.write(3 + static_cast<int>(rng() % 2), rng);
        }
    };
    auto reader = [&](unsigned seed) {
        (void)seed;
        while (!stop.load()) {
            Snapshot s;
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                s = tree.snapshot_locked_held();
            }
            long a, b;
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                a = tree.iterate_locked(s);
                b = tree.iterate_locked(s);
            }
            assert(a == b);
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                tree.release_locked(s);
            }
            iters.fetch_add(1);
        }
    };

#ifdef ASAN_MULTITHREAD
    std::vector<std::thread> pool;
    for (int i = 0; i < 2; ++i) pool.emplace_back(writer, 100 + i);
    for (int i = 0; i < 2; ++i) pool.emplace_back(reader, 500 + i);
    std::this_thread::sleep_for(std::chrono::seconds(2));
    stop.store(true);
    for (auto& th : pool) th.join();
#else
    std::mt19937 rng(7);
    for (int i = 0; i < 2000; ++i) {
        tree.write(3 + static_cast<int>(rng() % 2), rng);
        if ((i & 63) == 0) {
            Snapshot s;
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                s = tree.snapshot_locked_held();
            }
            long a, b;
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                a = tree.iterate_locked(s);
                b = tree.iterate_locked(s);
            }
            assert(a == b);
            {
                std::lock_guard<std::recursive_mutex> g(store.mu);
                tree.release_locked(s);
            }
        }
    }
    std::cout << "single-thread self-check done" << std::endl;
#endif

    std::cout << "ASAN harness: iterations=" << iters.load()
              << " allocs=" << store.allocs.load()
              << " frees=" << store.frees.load()
              << " uaf=" << store.uaf.load()
              << " refcnt_bad=" << store.refcnt_bad.load()
              << " peak_live=" << store.peak_live << std::endl;
    assert(store.uaf.load() == 0);
    assert(store.refcnt_bad.load() == 0);

    {
        std::lock_guard<std::recursive_mutex> g(store.mu);
        for (int pid : tree.collect_locked(tree.root)) store.unref_locked(pid);
        assert(store.live.empty());
    }
    assert(store.frees.load() == store.allocs.load());
    std::cout << "ASAN/UBSAN/LSAN: clean, exit 0" << std::endl;
    return 0;
}
