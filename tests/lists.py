"""Small edit decision lists for the tests."""


def cut(num, reel, track, si, so, ri, ro):
    return "%03d  %-8s %-5s C        %s %s %s %s" % (num, reel, track, si, so, ri, ro)


def dissolve(num, out_reel, in_reel, frames, si, so, ri, ro):
    """The two lines of a dissolve: outgoing placeholder, incoming side."""
    return ["%03d  %-8s V     C        %s %s %s %s" % (num, out_reel, ri, ri, ri, ri),
            "%03d  %-8s V     D    %03d %s %s %s %s" % (num, in_reel, frames, si, so, ri, ro)]


def edl(lines, fcm="NON-DROP FRAME"):
    return "\n".join(["TITLE: TEST", "FCM: " + fcm, ""] + lines) + "\n"
