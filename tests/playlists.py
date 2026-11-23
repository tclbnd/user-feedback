"""Small CPLs for the tests (2013 schema)."""


def cpl(image_durations):
    res = "".join("<Resource><Id>r%d</Id><IntrinsicDuration>%d</IntrinsicDuration><TrackFileId>t%d</TrackFileId></Resource>" % (i, d, i)
                  for i, d in enumerate(image_durations))
    return ('<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2013" '
            'xmlns:cc="http://www.smpte-ra.org/schemas/2067-2/2013"><ContentTitle>TEST</ContentTitle><EditRate>24 1</EditRate>'
            "<SegmentList><Segment><SequenceList><cc:MainImageSequence><ResourceList>%s</ResourceList></cc:MainImageSequence>"
            "</SequenceList></Segment></SegmentList></CompositionPlaylist>" % res).encode()


CPL_2016 = ('<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2016" '
            'xmlns:cc="http://www.smpte-ra.org/schemas/2067-2/2016">'
            '<ContentTitle>T2016</ContentTitle><ContentKind>trailer</ContentKind><EditRate>24 1</EditRate>'
            '<CompositionTimecode><TimecodeRate>24</TimecodeRate><TimecodeStartAddress>01:00:00:00</TimecodeStartAddress></CompositionTimecode>'
            '<SegmentList>'
            '<Segment><SequenceList>'
            '<cc:MainImageSequence><TrackId>v1</TrackId><ResourceList>'
            '<Resource><IntrinsicDuration>10</IntrinsicDuration><RepeatCount>5</RepeatCount><TrackFileId>f1</TrackFileId></Resource>'
            '<Resource><IntrinsicDuration>100</IntrinsicDuration><EntryPoint>10</EntryPoint><TrackFileId>f2</TrackFileId></Resource>'
            '</ResourceList></cc:MainImageSequence>'
            '<cc:MainAudioSequence><TrackId>a1</TrackId><ResourceList>'
            '<Resource><EditRate>48000 1</EditRate><IntrinsicDuration>290000</IntrinsicDuration><EntryPoint>10000</EntryPoint><TrackFileId>f3</TrackFileId></Resource>'
            '</ResourceList></cc:MainAudioSequence>'
            '<MarkerSequence><TrackId>m1</TrackId><ResourceList>'
            '<Resource><IntrinsicDuration>140</IntrinsicDuration><Marker><Label>A</Label><Offset>7</Offset></Marker></Resource>'
            '</ResourceList></MarkerSequence>'
            '</SequenceList></Segment>'
            '<Segment><SequenceList>'
            '<cc:MainImageSequence><TrackId>v1</TrackId><ResourceList>'
            '<Resource><IntrinsicDuration>50</IntrinsicDuration><TrackFileId>f2</TrackFileId></Resource>'
            '</ResourceList></cc:MainImageSequence>'
            '<cc:MainAudioSequence><TrackId>a2</TrackId><ResourceList>'
            '<Resource><EditRate>48000 1</EditRate><IntrinsicDuration>1000000</IntrinsicDuration><SourceDuration>40000</SourceDuration><TrackFileId>f4</TrackFileId></Resource>'
            '</ResourceList></cc:MainAudioSequence>'
            '<MarkerSequence><TrackId>m1</TrackId><ResourceList>'
            '<Resource><IntrinsicDuration>50</IntrinsicDuration><Marker><Label>B</Label><Offset>25</Offset></Marker></Resource>'
            '</ResourceList></MarkerSequence>'
            '</SequenceList></Segment>'
            '</SegmentList></CompositionPlaylist>').encode()
