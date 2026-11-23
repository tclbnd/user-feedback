"""Small CPLs for the tests (2013 schema)."""


def cpl(image_durations):
    res = "".join("<Resource><Id>r%d</Id><IntrinsicDuration>%d</IntrinsicDuration><TrackFileId>t%d</TrackFileId></Resource>" % (i, d, i)
                  for i, d in enumerate(image_durations))
    return ('<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2013" '
            'xmlns:cc="http://www.smpte-ra.org/schemas/2067-2/2013"><ContentTitle>TEST</ContentTitle><EditRate>24 1</EditRate>'
            "<SegmentList><Segment><SequenceList><cc:MainImageSequence><ResourceList>%s</ResourceList></cc:MainImageSequence>"
            "</SequenceList></Segment></SegmentList></CompositionPlaylist>" % res).encode()
