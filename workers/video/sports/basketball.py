"""Basketball-specific input configuration; general media/tracking stay reusable."""

# Manually marked playable 3x3 court in TrackID3x3 IMG_0104.MOV, normalized x/y.
# This is a court mask, not calibrated geometry or a player identity classifier.
OUTDOOR_SAMPLE_COURT = [[0.04, 0.91], [0.265, 0.535], [0.735, 0.535], [0.995, 0.86]]
