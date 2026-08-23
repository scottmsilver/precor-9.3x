# Dense Ridgeline Transition Bookends

## Context

The Ridgeline map keeps every visible interval-transition label present at
ordinary workout densities. Programs and reshaped workout histories may,
however, contain arbitrarily many short intervals. Projecting, packing,
measuring, and drawing every transition in a 600-second camera window makes
frame cost grow without bound, even though the labels eventually become too
dense to read.

The workout model must remain unrestricted and exact. Planned durations,
fractional skip history, route geometry, grade cells, transition ticks, and
persistence are not changed by this design. Only the label presentation for an
overfull viewport changes.

## Behavioral contract

When at most 64 transition boundaries are visible, the existing sticky-label
pipeline remains unchanged: every transition receives its normal grade/speed
chip, collision displacement, leader line, traveled-state styling, and marker
and metrics exclusions.

When more than 64 boundaries are visible, the viewport enters bookend mode:

- Divide the visible program-time window into exactly 32 equal-duration,
  contiguous buckets anchored to the same quantized camera position used by
  label packing. Buckets beyond the route end are empty.
- Ignore empty buckets. A bucket containing one boundary renders that normal
  transition chip. A bucket containing two or more boundaries renders the
  first and last transition chips joined by an amber bracket.
- The ending chip carries an inline, measured `×N` badge, where `N` is the
  number of underlying boundaries represented by that bracket. Counts above
  999 use bounded human-readable forms (`×1.2k`, `×2.1M`, or `×2.1B`) while the
  group model retains the exact integer count. Each visible boundary is
  represented by exactly one bucket: none are omitted from the aggregate count
  and none are counted twice.
- First and last chips retain their own grade, speed, anchor, and traveled
  styling. A bucket straddling the marker may therefore have a dimmed first
  chip and a current/future last chip.
- Bookend chips participate in the existing deterministic placement pipeline.
  Badge width is part of the ending chip's measured candidate footprint. The
  bracket follows the placed chips but is drawn behind chips, the marker, and
  the metrics panel, with its path masked out of their guard rectangles.

Crossing the 64-boundary threshold may switch presentation modes. Within a
mode, membership, prioritization, and placement use the quantized camera key,
the traveled boundary cut, and the existing snapped guard keys so subpixel
animation does not cause label flicker or unnecessary recomputation. Crossing a
represented boundary changes the traveled cut and therefore recomputes priority
and placement even when the camera key and snapped marker guard are unchanged.
Anchor dots and their ordinary anchor-to-pill leader endpoints still use exact
current-frame geometry and therefore move smoothly between packing-grid
boundaries.

## Boundary and bucket semantics

The label universe is interval-start indices `0 until route.count`; index `i`
has key `route.startOf(i)`. Route start `0` is a label when it is visible. Route
end `route.total` is not a label because it has no interval-start index.

Let `qLo` be the 2-pixel packing-grid camera position already derived by the
frame cache, and let `qHi = min(qLo + ew, route.total)`. Visible boundaries use
the existing inclusive rule `qLo <= key <= qHi`. Mode selection uses that exact
same set: 64 boundaries remain exact and 65 enter bookend mode. This narrows the
ordinary-mode guarantee only from exact animated `camLo` to the cache's existing
quantized packing window; exact anchors remain animated as described above.

The route exposes binary-search boundary accessors over its cumulative array:

- `firstBoundaryAtOrAfter(t)` returns the first label index whose key is `>= t`.
- `firstBoundaryAfter(t)` returns the first label index whose key is `> t`.

Both return an end-exclusive index in `0..route.count`. The visible index range
is therefore `[firstBoundaryAtOrAfter(qLo), firstBoundaryAfter(qHi))`.

Dense bucket `b` spans
`[qLo + b*ew/32, qLo + (b+1)*ew/32)`, except the final bucket includes `qHi`.
For each bucket, `first` is `firstBoundaryAtOrAfter(bucketStart)`, constrained
to the visible range. `endExclusive` is `firstBoundaryAtOrAfter(bucketEnd)` for
buckets 0–30 and `firstBoundaryAfter(qHi)` for bucket 31. The previous bucket's
end index is reused as the next bucket's first index. A non-empty group has
`last = endExclusive - 1` and `count = endExclusive - first`. Thus a boundary
on an internal bucket edge belongs to the later bucket exactly once.

## Bounded collection

Dense mode must not first enumerate every visible boundary. It determines the
visible index range with the route's binary-search boundary lookup, then probes
the 32 program-time buckets above. It never walks the boundaries between a
bucket's first and last index.

`RidgelineRoute.idxAt` is changed from a linear scan to binary search. The route
also precomputes cumulative switchback phase at every boundary, so `phaseAt`
combines one indexed lookup, one prefix value, and the partial current interval.
Consequently each selected `worldX` projection is `O(log N)` rather than hiding
an `O(N)` scan. Dense label collection and projection are bounded by a constant
number of indexed lookups and at most 64 projections, independent of the number
of interior transitions.

The resulting frame contains at most 64 projected labels, 32 brackets, and 32
count badges. Text preparation remains lazy and model-lifetime cached. Route
drawing and transition tick rendering keep their existing bounded sampling;
only transition-label collection and presentation change here.

## Data flow and ownership

The route remains the source of cumulative boundary times. A pure collection
helper converts the quantized camera window into either exact single-boundary
groups or dense bookend groups. Each group records its half-open interval-index
range, exact count, and first/last content identities.

The existing frame cache owns group membership, prioritization, measured badge
content, and slot decisions for the quantized camera, traveled boundary cut,
snapped marker guard, metrics guard, and map bounds. On every draw, the
first/last indices are reprojected through the exact current geometry; anchor
dots and ordinary anchor-to-pill leader endpoints use those exact positions
while slot choices remain stable until one of the cache keys changes. No
aggregated state is written back to the program, server, or database.

The badge is appended inside the ending pill after the speed run, with the same
horizontal padding and an amber text color. Its measured width enlarges that
candidate before placement, so ordinary collision checks cover the whole pill.
The one-pixel amber bracket joins the two *placed pills*. From their current
slot rectangles, choose a deterministic spine beside the route centerline and
draw one vertical segment between the pill centerlines plus horizontal arms
that terminate exactly at the nearest boundary of the first and ending pill.
This geometry is recomputed from the current slot rectangles whenever layout is
recomputed. Separate ordinary leader lines join each pill to its exact animated
route anchor, so the bracket communicates grouping while leaders communicate
route location. The bracket is drawn after the route but before all leader
lines, chips, marker, and metrics. Before drawing, its path is clipped against
the canvas and differenced with the inflated marker/metrics guards and every
placed chip rectangle except for the zero-area arm endpoints that touch its two
own pill boundaries. Brackets may cross one another or the route at low alpha,
but they cannot paint over guarded content or labels. A bracket uses the lower
of its two endpoint alphas; a group straddling the marker therefore remains
visually subordinate to its future ending chip.

## Edge cases

- Non-finite or non-positive route durations remain rejected by
  `RidgelineRoute` before collection.
- Viewport endpoints and bucket edges use the exact conventions defined above;
  internal bucket edges are half-open while the visible upper edge is included.
- A bucket whose first and last index are equal renders one ordinary label and
  no bracket or count badge.
- A cluster at the top or bottom canvas edge uses the same clipping and
  placement bounds as ordinary sticky labels; bracket caps are clipped too.
- If collision space is physically exhausted, the existing deterministic
  fallback behavior applies to the bounded bookend set rather than to every
  underlying transition.

## Verification

Focused unit tests must demonstrate:

1. Up to 64 visible transitions produce the same keys, contents, and ordinary
   label behavior as the existing pipeline; 64 is exact and 65 is dense.
2. More than 64 transitions produce at most 32 groups and 64 projected labels.
3. Group index ranges are contiguous, disjoint, cover every visible boundary
   exactly once, and report accurate `×N` counts.
4. Route start, route end, `qLo`, `qHi`, and internal bucket-edge cases obey the
   specified inclusion rules without gaps or duplicates.
5. Instrumented boundary access, label preparation, projection, and placement
   counters prove that dense collection performs no interior `labelAt`, text
   measurement, `phaseAt`/`worldX`, or candidate creation. Production limits
   are injectable into the pure helper so tests also cover all-singleton and
   mixed empty/singleton/aggregate bucket sets.
6. Subpixel camera motion reuses group membership and slot decisions while the
   traveled cut and snapped guards are unchanged; exact anchors and leader
   endpoints move smoothly. Crossing a quantization boundary recomputes
   deterministically. Crossing a represented boundary within one camera bucket
   and one snapped guard cell updates traveled styling, priority, and placement
   deterministically.
7. Marker, metrics, canvas, and other-chip exclusions hold for the badge-sized
   pills. Clipped bracket pixels/bounds do not enter those guards. With heavily
   displaced bookends, each bracket arm still terminates on its corresponding
   placed pill boundary.
8. First/last traveled styling is correct for a group that straddles the
   marker; bracket alpha follows the lower endpoint alpha.
9. Count formatting and candidate widths remain bounded for counts through the
   route's maximum `Int` index range.
10. The focused Ridgeline suite and full Android unit suite remain green.

Physical-device verification should run a deliberately dense route and confirm
that normal mode is visually unchanged, dense bookends and badges remain
legible, bracket arms remain attached to displaced pills while their independent
leader lines track the exact route anchors during marker motion, and frame
presentation remains smooth.

## Out of scope

- Rejecting, truncating, or rewriting workout intervals.
- Changing the 600-second camera window, route geometry, transition ticks, or
  server timeline.
- Adding an interaction to expand a cluster; the badge communicates density
  without introducing a new touch target.
