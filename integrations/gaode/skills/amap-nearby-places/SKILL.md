---
name: amap-nearby-places
description: Recommend nearby places around a user location with real AMap (Gaode) POI and real walking-route data, filtering by a walking-time limit and returning at most three options. Use when the user asks what is worth visiting nearby, or asks in Chinese for 周边有什么、附近找、步行几分钟内、推荐附近地点. Triggers include 高德、地图、周边、附近、就近、步行、咖啡馆、餐厅、公园、nearby, walking, amap, gaode, POI.
---

# AMap nearby places with walking routes

Answer "what is worth going to nearby?" with places and walking routes that come from the
高德地图 MCP server. Every place, address, distance and duration in the answer must come from a
tool result. Never estimate a walking time from a straight-line distance.

适用场景：用户给出城市、起点坐标和一句自然语言需求（想找什么、最多推荐几家、步行几分钟内、
是否优先近一点），需要一份可以直接照着走的周边清单。
不适用于：驾车或公共交通导航、行政区划与天气查询、真实定位设备获取。

## Tools

Use the `amap-maps` MCP server. Inspect the host's available tools first and use the **full names
the host exposes**, including any namespace or prefix; do not construct names yourself. The base
names below are the reference — if the host lists `amap_maps_around_search`, call that, not
`maps_around_search`.

| Operation | Base tool name | Input | What comes back |
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

2. **Search for candidates.** Call the nearby-search tool with the origin `location`, the place
   type as `keywords`, and a `radius` that comfortably covers the walking limit — roughly
   `minutes × 80 m/min × 1.4` (15 minutes → about 1700 m). Request more candidates than the final
   count (5–8) because walking time will remove some of them. If nothing relevant comes back, retry
   once with the keyword-search tool using the same `keywords` and the request's city. Merge both
   result sets and **drop duplicates by POI `id`**, so one place is never recommended twice.

3. **Resolve coordinates.** For each candidate that could plausibly qualify, call the place-detail
   tool with the POI `id` to get its `location`. Keep the POI `name` and `address` exactly as the
   search returned them.

4. **Measure the real walk.** Call the walking-route tool with `origin` = the origin coordinates
   and `destination` = the candidate's `location`. Take `paths[0].distance` and `paths[0].duration`.
   Keep both raw values: the distance in **meters** and the duration in **seconds**. The walking
   minutes you report are `ceil(duration / 60)`; reproduce the numbers from the tool result, do not
   round-trip them through a different measure or a second estimate.

5. **Filter.** Keep only candidates whose walking duration is within the limit. A place that
   exceeds the limit is not a qualifying recommendation and must not be presented as one. If the
   request gave no limit, keep every candidate but still report the walking time.

6. **Order.** When the request asks for nearer-first ("优先近一点", "近一点的"), sort by walking
   distance ascending, then by walking duration ascending. Otherwise sort by walking distance
   ascending. The stated order must match the route data you reported.

7. **Answer.** Take at most the requested count (never more than 3 unless the request explicitly asks
   for more). Present them as a table with: place name, address, walking distance, walking minutes
   and one recommendation reason. Every value in the table comes from the tool results.

## Answer format

```markdown
从 <起点描述> 出发，步行 <N> 分钟内为你找到 <实际数量> 个符合条件的 <地点类型>：

| 名称 | 地址 | 步行距离 | 步行时间 | 推荐理由 |
| --- | --- | --- | --- | --- |
| <name> | <address> | <distance> 米 | <duration> 分钟 | <一句话理由> |
```

`<distance>` is the `distance` value from the walking-route result in meters, and `<duration>` is
`ceil(duration / 60)` from the same result. When the tool returned several paths, use
`paths[0]`. Do not report a distance or a duration that does not appear in a walking-route result.

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

## Worked example

Request: 我在杭州凤起路地铁站，想找一家咖啡馆，步行不超过 15 分钟，推荐最多 3 家，优先近一点的。

1. Place type 咖啡馆, limit 15 min, count 3, origin = the given station coordinates, order nearest-first.
2. Nearby search with `keywords=咖啡馆`, `radius=1700`, ask for 6 candidates.
3. Place-detail lookup for each candidate that could still qualify.
4. Walking-route lookup from the station to each candidate; keep distance (m) and duration (s).
5. Drop any candidate whose duration exceeds 900 s.
6. Sort the survivors by distance ascending.
7. Return at most 3 rows, each with the name, address, distance in meters, `ceil(duration/60)`
   minutes and one grounded reason; if fewer than 3 qualify, say so and why.

## Rules

- **Real data only.** Place names, addresses, distances and durations come from tool results. Never
  invent a place, an address, a distance or a duration. A plausible-looking POI that no tool
  returned is a failure, not a recommendation.
- **No placeholders.** Never output an example, template or fictional place as if it were a real
  result. If the search failed, report the failure instead of filling the table.
- **Walking time comes from the route query.** Never substitute `maps_distance`, a straight-line
  calculation, or a guess for the walking duration.
- **One origin, independent options.** Each place is an alternative reachable from the same origin.
  Do not chain them into a multi-stop itinerary, and do not present a second leg.
- **Respect the count.** Return at most the requested number, and at most 3 by default.
- **Respect the time limit.** Only list places that the walking route confirms are within it.
- **Mark the unverified.** Quietness, queue length, opening status, ratings and popularity are
  usually not in the tool results. Label them "未核实" instead of asserting them, or leave them out.
- **Failure is a valid answer.** An empty or partial result that explains itself scores better than
  a fabricated one.
- **No credentials.** The API key is provided by the host. Never write keys or tokens into the
  answer or into any file.
