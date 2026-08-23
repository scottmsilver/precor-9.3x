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

- Divide the visible program-time window into at most 32 stable, contiguous
  buckets using the same quantized camera position used by label layout.
- Ignore empty buckets. A bucket containing one boundary renders that normal
  transition chip. A bucket containing two or more boundaries renders the
  first and last transition chips joined by an amber bracket.
- The ending chip carries a compact `×N` badge, where `N` is the number of
  underlying boundaries represented by that bracket. Each visible boundary is
  represented by exactly one bucket: none are omitted from the aggregate count
  and none are counted twice.
- First and last chips retain their own grade, speed, anchor, and traveled
  styling. A bucket straddling the marker may therefore have a dimmed first
  chip and a current/future last chip.
- Bookend chips participate in the existing deterministic placement pipeline.
  The bracket and badge follow the placed chips and must not obscure the
  position marker or metrics panel.

Crossing the 64-boundary threshold may switch presentation modes. Within a
mode, membership and placement use the quantized camera key so subpixel
animation does not cause label flicker or unnecessary recomputation.

## Bounded collection

Dense mode must not first enumerate every visible boundary. It determines the
visible index range with the route's indexed/binary-search lookup, then probes
at most 32 program-time buckets. Each bucket resolves its first and last
boundary index without walking the boundaries between them.

The resulting frame contains at most 64 projected labels, 32 brackets, and 32
count badges. Text preparation remains lazy and model-lifetime cached. Route
drawing and transition tick rendering keep their existing bounded sampling;
only transition-label collection and presentation change here.

## Data flow and ownership

The route remains the source of cumulative boundary times. A pure collection
helper converts the current camera window into either exact single-boundary
groups or dense bookend groups. Each group records its inclusive interval-index
range and count plus the projected first/last labels needed for placement.

The existing frame cache owns the derived groups and placement result for the
quantized camera, marker guard, metrics guard, and map bounds. The draw pass
consumes that cached result and draws normal labels or the bracket/badge
decoration. No aggregated state is written back to the program, server, or
database.

## Edge cases

- Non-finite or non-positive route durations remain rejected by
  `RidgelineRoute` before collection.
- Viewport endpoints use one consistent half-open boundary convention so a
  transition at a bucket edge belongs to exactly one bucket.
- A bucket whose first and last index are equal renders one ordinary label and
  no bracket or count badge.
- A cluster at the top or bottom canvas edge uses the same clipping and
  placement bounds as ordinary sticky labels.
- If collision space is physically exhausted, the existing deterministic
  fallback behavior applies to the bounded bookend set rather than to every
  underlying transition.

## Verification

Focused unit tests must demonstrate:

1. Up to 64 visible transitions produce the same keys, contents, and ordinary
   label behavior as the existing pipeline.
2. More than 64 transitions produce at most 32 groups and 64 projected labels.
3. Group index ranges are contiguous, disjoint, cover every visible boundary
   exactly once, and report accurate `×N` counts.
4. Dense collection does not call `labelAt` or project `worldX` for interior
   boundaries.
5. Subpixel camera motion reuses the cached grouping and placement; crossing a
   quantization boundary recomputes deterministically.
6. Marker and metrics exclusion rules still hold for placed bookends.
7. First/last traveled styling is correct for a group that straddles the
   marker.
8. The focused Ridgeline suite and full Android unit suite remain green.

Physical-device verification should run a deliberately dense route and confirm
that normal mode is visually unchanged, dense bookends and badges remain
legible, bracket endpoints track their chips during marker motion, and frame
presentation remains smooth.

## Out of scope

- Rejecting, truncating, or rewriting workout intervals.
- Changing the 600-second camera window, route geometry, transition ticks, or
  server timeline.
- Adding an interaction to expand a cluster; the badge communicates density
  without introducing a new touch target.
