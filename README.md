# tagread

Episode and track uploads carry their metadata in ID3v2 tags. The catalogue
reads the text frames (title, artists, album, genre), comments and
user-defined TXXX frames, and the podcast feed is built from that summary.
Text frames are reported as lists because a frame can hold several values.

## Layout

- `tagread/id3.py` – tag header and frame walking
- `tagread/frames.py` – text, comment and TXXX frames
- `tagread/summary.py` – catalogue summary
- `tagread/feed.py` – podcast feed fields
- `id3dump.py` – command line

## Running

    python3 id3dump.py examples/*.mp3
    python3 -m unittest discover -s tests
