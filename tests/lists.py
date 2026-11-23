"""Small edit decision lists for the tests."""


def cut(num, reel, track, si, so, ri, ro):
    return "%03d  %-8s %-5s C        %s %s %s %s" % (num, reel, track, si, so, ri, ro)


def edl(lines):
    return "\n".join(["TITLE: TEST", "FCM: NON-DROP FRAME", ""] + lines) + "\n"
