"""Write-ahead log: one JSON record per line."""
import json
import os


class Log:
    def __init__(self, path):
        self.path = path
        if not os.path.exists(path):
            open(path, "wb").close()
        self._discard_partial_tail()
        records = self.records()
        self.last = records[-1]["lsn"] if records else 0

    def _discard_partial_tail(self):
        """Drop an interrupted append at the end of the log.

        A record is durable only once its full line (including the trailing
        newline) is on disk. If the file ends in bytes without a newline, or
        those bytes are not valid JSON, they came from a write interrupted by
        a power loss and must be removed before the log is appended to again.
        """
        with open(self.path, "rb") as fh:
            data = fh.read()
        valid_len = 0
        for line in data.split(b"\n"):
            if not line:
                continue  # final empty piece after the last newline
            try:
                json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                break
            valid_len += len(line) + 1
        if valid_len != len(data):
            with open(self.path, "r+b") as fh:
                fh.truncate(valid_len)

    def records(self):
        """Parse complete log records.

        Each record occupies one line terminated by '\\n'. A final line without
        a trailing newline is an interrupted append (crash mid-write) and must
        be ignored, along with any line that fails to parse as JSON.
        """
        out = []
        with open(self.path, "rb") as fh:
            data = fh.read()
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                # Corrupted / partially written tail record: stop here.
                break
        return out

    def append(self, record):
        self.last += 1
        record = {"lsn": self.last, **record}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record
