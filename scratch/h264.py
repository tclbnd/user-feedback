import struct, sys

class BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.bits = len(data) * 8

    def u1(self):
        if self.pos >= self.bits:
            raise EOFError("eof")
        byte = self.data[self.pos >> 3]
        v = (byte >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return v

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | self.u1()
        return v

    def ue(self):
        zeros = 0
        while True:
            b = self.u1()
            if b:
                break
            zeros += 1
            if zeros > 31:
                raise ValueError("ue too long")
        if zeros == 0:
            return 0
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self):
        k = self.ue()
        if k & 1:
            return (k + 1) // 2
        return -(k // 2)

    def left(self):
        return self.bits - self.pos

def remove_epb(data):
    out = bytearray()
    zeros = 0
    for b in data:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        if b == 0:
            zeros += 1
        else:
            zeros = 0
    return bytes(out)

HIGH_PROFILES = (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135)

def parse_sps(rbsp):
    br = BitReader(rbsp)
    profile = br.u(8)
    constraints = br.u(8)
    level = br.u(8)
    sps_id = br.ue()
    chroma_format_idc = 1
    if profile in HIGH_PROFILES:
        chroma_format_idc = br.ue()
        if chroma_format_idc == 3:
            br.u1()
        br.ue()
        br.ue()
        br.u1()
        if br.u1():
            count = 8 if chroma_format_idc != 3 else 12
            for _ in range(count):
                if br.u1():
                    last = 8
                    nxt = 8
                    for _ in range(16):
                        if nxt != 0:
                            nxt = (last + br.se() + 256) % 256
                        last = nxt if nxt != 0 else last
    log2_max_frame_num_minus4 = br.ue()
    pic_order_cnt_type = br.ue()
    log2_max_poc_lsb_minus4 = None
    delta_pic_order_always_zero_flag = 0
    if pic_order_cnt_type == 0:
        log2_max_poc_lsb_minus4 = br.ue()
    elif pic_order_cnt_type == 1:
        delta_pic_order_always_zero_flag = br.u1()
        br.se()
        br.se()
        count = br.ue()
        for _ in range(count):
            br.se()
    max_num_ref_frames = br.ue()
    gaps_in_frame_num_allowed_flag = br.u1()
    pic_width_in_mbs_minus1 = br.ue()
    pic_height_in_map_units_minus1 = br.ue()
    frame_mbs_only_flag = br.u1()
    if not frame_mbs_only_flag:
        br.u1()
    direct_8x8 = br.u1()
    frame_cropping_flag = br.u1()
    crop = [0, 0, 0, 0]
    if frame_cropping_flag:
        crop = [br.ue(), br.ue(), br.ue(), br.ue()]
    vui_present = br.u1()
    return {
        "sps_id": sps_id,
        "profile": profile,
        "level": level,
        "chroma_format_idc": chroma_format_idc,
        "log2_max_frame_num": log2_max_frame_num_minus4 + 4,
        "pic_order_cnt_type": pic_order_cnt_type,
        "log2_max_poc_lsb": (None if log2_max_poc_lsb_minus4 is None else log2_max_poc_lsb_minus4 + 4),
        "delta_pic_order_always_zero_flag": delta_pic_order_always_zero_flag,
        "max_num_ref_frames": max_num_ref_frames,
        "frame_mbs_only_flag": frame_mbs_only_flag,
        "width_mbs": pic_width_in_mbs_minus1 + 1,
        "height_map_units": pic_height_in_map_units_minus1 + 1,
        "crop": crop,
        "vui_present": vui_present,
    }

def parse_pps(rbsp):
    br = BitReader(rbsp)
    pps_id = br.ue()
    sps_id = br.ue()
    entropy_coding_mode = br.u1()
    bottom_field_pic_order_in_frame_present_flag = br.u1()
    num_slice_groups_minus1 = br.ue()
    if num_slice_groups_minus1 > 0:
        raise NotImplementedError("slice groups unsupported")
    num_ref_idx_l0_default_active_minus1 = br.ue()
    num_ref_idx_l1_default_active_minus1 = br.ue()
    weighted_pred_flag = br.u1()
    weighted_bipred_idc = br.u(2)
    pic_init_qp_minus26 = br.se()
    pic_init_qs_minus26 = br.se()
    chroma_qp_index_offset = br.se()
    deblocking_filter_control_present_flag = br.u1()
    constrained_intra_pred_flag = br.u1()
    redundant_pic_cnt_present_flag = br.u1()
    transform_8x8 = 0
    scaling = 0
    if br.left() > 0:
        transform_8x8 = br.u1()
        scaling = br.u1()
    return {
        "pps_id": pps_id,
        "sps_id": sps_id,
        "entropy_coding_mode": entropy_coding_mode,
        "bottom_field_pic_order_in_frame_present_flag": bottom_field_pic_order_in_frame_present_flag,
        "num_ref_idx_l0_default_active_minus1": num_ref_idx_l0_default_active_minus1,
        "num_ref_idx_l1_default_active_minus1": num_ref_idx_l1_default_active_minus1,
        "redundant_pic_cnt_present_flag": redundant_pic_cnt_present_flag,
        "transform_8x8": transform_8x8,
    }

SLICE_NAMES = {0: "P", 1: "B", 2: "I", 3: "SP", 4: "SI", 5: "P", 6: "B", 7: "I", 8: "SP", 9: "SI"}

def parse_slice_header(rbsp, sps, pps, nri, ntype):
    br = BitReader(rbsp)
    first_mb = br.ue()
    slice_type = br.ue()
    pps_id = br.ue()
    frame_num = br.u(sps["log2_max_frame_num"])
    field_pic_flag = 0
    bottom_field_flag = 0
    if not sps["frame_mbs_only_flag"]:
        field_pic_flag = br.u1()
        if field_pic_flag:
            bottom_field_flag = br.u1()
    idr_pic_id = None
    if ntype == 5:
        idr_pic_id = br.ue()
    poc_lsb = None
    delta_poc_bottom = 0
    delta_poc = [0, 0]
    if sps["pic_order_cnt_type"] == 0:
        poc_lsb = br.u(sps["log2_max_poc_lsb"])
        if pps["bottom_field_pic_order_in_frame_present_flag"] and not field_pic_flag:
            delta_poc_bottom = br.se()
    elif sps["pic_order_cnt_type"] == 1:
        delta_poc[0] = br.se()
        if pps["bottom_field_pic_order_in_frame_present_flag"] and not field_pic_flag:
            delta_poc[1] = br.se()
    redundant_pic_cnt = 0
    if pps["redundant_pic_cnt_present_flag"]:
        redundant_pic_cnt = br.ue()
    direct_spatial_mv_pred_flag = None
    num_ref_l0 = pps["num_ref_idx_l0_default_active_minus1"] + 1
    num_ref_l1 = pps["num_ref_idx_l1_default_active_minus1"] + 1
    if slice_type in (1, 6):
        direct_spatial_mv_pred_flag = br.u1()
    if slice_type in (1, 6, 3, 8, 4, 9):
        if br.u1():
            num_ref_l0 = br.ue() + 1
            if slice_type in (1, 6):
                num_ref_l1 = br.ue() + 1
    l0_mods = []
    l1_mods = []
    if slice_type not in (2, 4, 7, 9):
        if br.u1():
            while True:
                idx = br.ue()
                if idx == 3:
                    break
                l0_mods.append((idx, br.ue()))
    if slice_type in (1, 6):
        if br.u1():
            while True:
                idx = br.ue()
                if idx == 3:
                    break
                l1_mods.append((idx, br.ue()))
    adaptive = None
    if nri != 0:
        if ntype == 5:
            br.u1()
            br.u1()
        else:
            adaptive = br.u1()
            if adaptive:
                while True:
                    op = br.ue()
                    if op == 0:
                        break
                    if op in (1, 3):
                        br.ue()
                    if op == 2:
                        br.ue()
                    if op in (3, 6):
                        br.ue()
                    if op == 4:
                        br.ue()
    cabac_init_idc = None
    if pps["entropy_coding_mode"] and slice_type not in (2, 4, 7, 9):
        cabac_init_idc = br.ue()
    slice_qp_delta = br.se()
    return {
        "first_mb": first_mb,
        "slice_type": slice_type,
        "pps_id": pps_id,
        "frame_num": frame_num,
        "field_pic_flag": field_pic_flag,
        "bottom_field_flag": bottom_field_flag,
        "idr_pic_id": idr_pic_id,
        "poc_lsb": poc_lsb,
        "delta_poc_bottom": delta_poc_bottom,
        "delta_poc": delta_poc,
        "redundant_pic_cnt": redundant_pic_cnt,
        "direct_spatial_mv_pred": direct_spatial_mv_pred_flag,
        "num_ref_l0": num_ref_l0,
        "num_ref_l1": num_ref_l1,
        "l0_mods": l0_mods,
        "l1_mods": l1_mods,
        "adaptive_marking": adaptive,
        "slice_qp_delta": slice_qp_delta,
        "bits_used": br.pos,
        "bits_total": br.bits,
    }

def split_nal(data, length_size):
    nals = []
    off = 0
    n = len(data)
    while off + length_size <= n:
        size = int.from_bytes(data[off:off+length_size], "big")
        off += length_size
        nals.append(data[off:off+size])
        off += size
    return nals

def nal_type(nal):
    return nal[0] & 31

def nal_ref_idc(nal):
    return (nal[0] >> 5) & 3
