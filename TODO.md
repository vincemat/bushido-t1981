# To do

Ideas not implemented yet. Remove a line when it is done.

## Next

- **Training plan towards a target event.** Plan the training before entering the goal in
  "Suivi". The page shows where the rider stands, not what to do each week: weekly volume
  targets, key workouts, long rides, progression to the date.
- **Recalibrate once the bike is properly mounted** (new rear skewer or thru-axle adapter),
  then tune "Élan simulé" on a real ride.

## Known defects (code review of 2026-11-16, not fixed yet)

- `bushido/route.py`: a GPX point without `lat` or `lon` raises `KeyError`, which nothing
  catches: the upload answers 500 instead of 400, and one such file in `routes/` empties the
  whole library on the home page. Turn it into a `ValueError`.
- `bushido/library.py`: `PROFILE_NAME` accepts `.` and `..` as rider names, so that rider's
  sessions would land in the sessions folder itself or its parent. Reject names made of dots.
- `bushido/trainer.py`: `switch_profile` stops the running session before checking the name,
  so a refused profile change (duplicate, unknown) still ends the recording. Validate first.
- Dead code: `DAY_MS` in `static/app.js`; `KMH_PER_MS` redefined in `export.py` instead of
  using `constants.KMH_PER_MS`.

## Riding screen

- When an ERG target is below what the brake can reach (tyre drag is about 14 N, so roughly
  100 W at 27 km/h), say "shift down and slow down" instead of "ease off".
- Heart rate from an ANT+ strap, on a second channel of the same stick.
- Surprise challenges on a free ride ("sprint 15 s", "hold 200 W for 2 min") with a score.
- Animated view of the route: a rider on the climb profile, the ghost ahead or behind.
- A real map (OpenStreetMap) instead of the bare plan of the track.

## Progress page

- The label above a weekly bar can overlap the goal line when both are at the same height.
- Weekly goal in hours as well as in kilometres.
- Import outdoor rides from Strava or a GPS file instead of typing them in.
- Edit or delete a home-trainer session; rename or delete a profile.

## Connections

- Bridge to Zwift / MyWhoosh and similar apps. Two routes, undecided: a second ANT stick
  exposing ANT+ FE-C (proven by FortiusANT, the app runs on the same Mac), or a spare Mac
  as a Bluetooth FTMS relay (no purchase, to be validated). Notes in the README credits.
- Use the T1982 head unit (it powers on, screen dead) as a reference: sniff what it sends to
  the brake to learn Tacx's own inertia and calibration handling.

## Housekeeping

- Automatic backup of `sessions/` and `profiles.json`: they live only on the
  computer running the app, never in git.
- Decode pages `04` (motor voltage/current) and `ad`, and bytes 5-6 of page `01`.
- Check whether the calibration value survives a power loss, by reading it back from the brake.
- FIT export next to TCX; confirm the TCX upload on Strava with a real session.
- A favicon (the only 404 left in the browser console).
