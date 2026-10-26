# AMap (Gaode) nearby places Skill

This directory contains a portable Agent Plugin package for the 高德地图 (AMap / Gaode) MCP
server. It provides one Skill, `amap-nearby-places`, that turns a natural-language request such as
"我在杭州某地铁站，想找一家咖啡馆，步行不超过 15 分钟，推荐最多 3 家，优先近一点的" into a short
list of nearby places backed by real POI and real walking-route data.

## What the Skill does

1. Reads the place type, walking-time limit, count and ranking preference from the request.
2. Searches nearby POIs with `maps_around_search` (fallback: `maps_text_search`).
3. Resolves each candidate's coordinates with `maps_search_detail`, because the search results do
   not contain them.
4. Measures the real walk with `maps_direction_walking` for every candidate.
5. Keeps only places inside the walking-time limit, sorts them nearest-first, and returns at most
   the requested number (3 by default).
6. Presents a table with name, address, walking distance, walking minutes and one grounded
   recommendation reason, plus a note when results are short or a call failed.

## Data honesty rules encoded in the Skill

- Place names, addresses, distances and durations always come from tool results.
- Walking time always comes from the walking-route query. `maps_distance` measures straight-line
  separation and must not be used to judge the walking limit or to state a walking time.
- Search results carry no coordinates, so a place-detail lookup is always required before routing.
- Quietness, queue length, opening status and ratings are not returned by the tools; the Skill
  marks them "未核实" instead of asserting them.
- An empty or failed lookup is reported as such. Fabricated places score worse than fewer places.

## Package layout

```text
integrations/gaode/
├── README.md
├── plugin.json
├── mcp.json
└── skills/
    └── amap-nearby-places/
        └── SKILL.md
```

## MCP server configuration

`mcp.json` registers the official AMap MCP server over stdio:

```json
{
  "mcpServers": {
    "amap-maps": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@amap/amap-maps-mcp-server"],
      "env": { "AMAP_MAPS_API_KEY": "${AMAP_MAPS_API_KEY}" }
    }
  }
}
```

The host supplies `AMAP_MAPS_API_KEY`. Never commit a key or write one into the Skill or the
answer.

The same server is available on ModelScope as `@amap/amap-maps`, and the AMap open platform
offers an SSE endpoint at `https://mcp.amap.com/sse?key=<AMAP_MAPS_API_KEY>` for hosts that
prefer a hosted connection.

## Tools used

| Tool | Purpose |
| --- | --- |
| `maps_around_search` | Nearby POI search around the origin |
| `maps_text_search` | Keyword POI search, used as a fallback |
| `maps_search_detail` | Place detail, provides the coordinates needed for routing |
| `maps_direction_walking` | Walking route, provides distance (meters) and duration (seconds) |
| `maps_regeocode` | Optional: describe the origin in address terms |

Use the full tool names the host exposes, including any namespace or prefix.
