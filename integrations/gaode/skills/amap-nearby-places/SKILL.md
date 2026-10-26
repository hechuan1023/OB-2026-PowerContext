---
name: amap-nearby-places
description: Recommend nearby places around a user location with real AMap (Gaode) data and real walking routes, filtering by walking-time limit and returning at most three options. Use when the user asks "nearby ...", "what is worth visiting around here", "cafes/restaurants/parks within N minutes on foot", or 周边有什么、附近找、步行几分钟内、推荐附近地点. Triggers: 高德、地图、周边、附近、就近、步行、咖啡馆、餐厅、公园、nearby, walking, amap, gaode, POI.
---

# AMap nearby places with walking routes

Answer "what is worth going to nearby?" with places and walking routes that come from the
高德地图 MCP server. Every place, address, distance and duration in the answer must come from a
tool result. Never estimate a walking time from a straight-line distance.

## Tools

Use the `amap-maps` MCP server. Inspect the host's available tools first and use the **full names
the host exposes**, including any namespace or prefix; do not construct names yourself. The base
names below are the reference.

| Operation | Tool | Input | What comes back |
| --- | --- | --- | --- |
| Nearby search | `maps_around_search` | `location` (origin lng,lat), `keywords` (place type), `radius` (meters, optional) | `pois[]`: `id`, `name`, `address`, `typecode`, `photos` |
| Keyword search | `maps_text_search` | `keywords`, `city` (optional), `types` (optional), `citylimit` (optional) | `suggestion`, `pois[]` with the same fields |
| Place detail | `maps_search_detail` | `id` (a POI id) | `location` (lng,lat), `address`, `business_area`, `city`, `type` |
| Walking route | `maps_direction_walking` | `origin` (lng,lat), `destination` (lng,lat) | `route.paths[]`: `distance` (**meters**), `duration` (**seconds**), `steps[]` |
| Reverse geocode | `maps_regeocode` | `location` | `addressComponent` for describing the origin |
| Straight-line distance | `maps_distance` | `origins`, `destination`, `type` | `results[]`: `distance` (meters), `duration` |

Two constraints drive the whole procedure:

- `maps_around_search` and `maps_text_search` return **no coordinates**. `maps_direction_walking`
  needs coordinates, so every candidate needs a `maps_search_detail` call first.
- `maps_distance` measures straight-line separation. **Never** use it to decide whether a place is
  within the walking-time limit or to state a walking time. Only `maps_direction_walking` provides
  a real walking duration.

## Procedure

1. **Read the request.** Extract the place type ("咖啡馆", "餐厅", "公园"), the walking-time limit
   ("步行不超过 15 分钟"), the count ("最多 3 家"), the origin, and any ranking preference
   ("优先近一点"). The origin coordinates come from the request. Defaults when the request is
   silent: at most 3 places and a 15-minute walking limit.

2. **Search for candidates.** Call `maps_around_search` with the origin `location`, the place type
   as `keywords`, and a `radius` that comfortably covers the walking limit — roughly
   `minutes × 80 m/min × 1.4` (15 minutes → about 1700 m). Request more candidates than the final
   count (5–8) because walking time will remove some of them. If nothing relevant comes back, retry
   once with `maps_text_search` using the same `keywords` and the request's city.

3. **Resolve coordinates.** For each candidate that could plausibly qualify, call
   `maps_search_detail` with the POI `id` to get its `location`. Keep the POI `name` and `address`
   exactly as the search returned them.

4. **Measure the real walk.** Call `maps_direction_walking` with `origin` = the origin coordinates
   and `destination` = the candidate's `location`. Take `paths[0].distance` and `paths[0].duration`.
   Convert to minutes with `ceil(duration / 60)` and report the distance in meters. These numbers
   must be reproduced from the tool result; do not round-trip them through a different measure.

5. **Filter.** Keep only candidates whose walking duration is within the limit. A place that
   exceeds the limit is not a qualifying recommendation and must not be presented as one. If the
   request gave no limit, keep every candidate but still report the walking time.

6. **Order.** When the request asks for nearer-first ("优先近一点", "近一点的"), sort by walking
   distance ascending, then by walking duration ascending. Otherwise sort by walking distance
   ascending. The stated order must match the route data you reported.

7. **Answer.** Take at most the requested count (never more than 3 unless the request explicitly asks
   for more). Present them as a table with: place name, address, walking distance, walking minutes,
   and one recommendation reason. Every value in the table comes from the tool results.

## Answer format

```markdown
从 <起点描述> 出发，步行 <N> 分钟内为你找到 <实际数量> 个符合条件的 <地点类型>：

| 名称 | 地址 | 步行距离 | 步行时间 | 推荐理由 |
| --- | --- | --- | --- | --- |
| <name> | <address> | <distance> 米 | <duration> 分钟 | <一句话理由> |
```

Close with a short note when any of these applies:

- Fewer results than requested: say how many were found and that the rest fell outside the walking
  limit or were not returned by the search, and suggest relaxing the time limit or the place type.
- No results at all: say that nothing matching was found within the searched range.
- A tool call failed: name the failed operation and the returned reason. Do not present a partial
  or invented answer as if the lookup succeeded.

The recommendation reason must be grounded in what the tools returned — the place type, its
address or business area, and the walking distance. Example: "同类型门店，位于<商圈>，步行约
<distance> 米". Do not write reasons such as "安静""不用排队""现在营业""评分很高" unless a tool
returned that information; if it was not returned, either leave it out or mark it "未核实".

## Rules

- **Real data only.** Place names, addresses, distances and durations come from tool results. Never
  invent a place, an address, a distance or a duration. A plausible-looking POI that no tool
  returned is a failure, not a recommendation.
- **Walking time comes from the route query.** Never substitute `maps_distance`, a straight-line
  calculation, or a guess for the walking duration.
- **One origin, independent options.** Each place is an alternative reachable from the same origin.
  Do not chain them into a multi-stop itinerary, and do not present a second leg.
- **Respect the count.** Return at most the requested number, and at most 3 by default.
- **Respect the time limit.** Only list places that the walking route confirms are within it.
- **Mark the unverified.** Quieteness, queue length, opening status, ratings and popularity are
  usually not in the tool results. Label them "未核实" instead of asserting them, or leave them out.
- **Failure is a valid answer.** An empty or partial result that explains itself scores better than
  a fabricated one.
- **No credentials.** The API key is provided by the host. Never write keys or tokens into the
  answer or into any file.
