# voice-router API Diagrams (archify-react)

This document contains workflow diagrams for each voice-router API endpoint, built using the `archify-react` library. Each diagram shows the command, its path through the system, and the results.

## Overview

The `archify-react` library draws workflow diagrams in React using a simple spec format with nodes, edges, lanes, and states (`used`, `skipped`, `failed`). Each diagram visualizes the flow of a request through the voice-router system.

---

## 1. Sync House

### `POST /sync`

```typescript
const syncSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" }
  ],
  nodes: [
    { id: "n1", label: "Receive sync body", kind: "start", lane: "api" },
    { id: "n2", label: "Load previous house words", kind: "step", lane: "api" },
    { id: "n3", label: "Prune shadow words", kind: "step", lane: "api" },
    { id: "n4", label: "Rebuild house from sync", kind: "step", lane: "api" },
    { id: "n5", label: "Save to SQLite store", kind: "step", lane: "api" },
    { id: "n6", label: "Return entities & version", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "sync payload" },
    { id: "e2", source: "n2", target: "n3", label: "previous words" },
    { id: "e3", source: "n3", target: "n4", label: "pruned entities" },
    { id: "e4", source: "n4", target: "n5", label: "new version" },
    { id: "e5", source: "n5", target: "n6", label: "commit" },
  ],
};
```

**Result:** `{"entities": <count>, "alias_version": "<version>"}`

---

## 2. Route Command

### `POST /route`

```typescript
const routeSpec: DiagramSpec = {
  version: 1,
  direction: "RIGHT",
  lanes: [
    { id: "ha", label: "Home Assistant" },
    { id: "api", label: "API" },
    { id: "jev", label: "Jev (OpenRouter)" },
    { id: "kev", label: "Kev (local)" },
    { id: "store", label: "SQLite Store" },
  ],
  nodes: [
    { id: "n1", label: "Receive command text", kind: "start", lane: "ha" },
    { id: "n2", label: "Filter shadow options", kind: "step", lane: "api" },
    { id: "n3", label: "Ask Kev locally", kind: "step", lane: "kev" },
    { id: "n4", label: "Ask Jev (OpenRouter)", kind: "step", lane: "jev" },
    { id: "n5", label: "Store decision in record", kind: "step", lane: "store" },
    { id: "n6", label: "Wait Kev grace (0.05s)", kind: "step", lane: "kev" },
    { id: "n7", label: "Merge Kev answer if early", kind: "step", lane: "api" },
    { id: "n8", label: "Return decision to HA", kind: "end", lane: "ha" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "text + context" },
    { id: "e2", source: "n2", target: "n3", label: "shadow opts?" },
    { id: "e3", source: "n2", target: "n4", label: "no shadow opts" },
    { id: "e4", source: "n3", target: "n5", label: "kev result" },
    { id: "e5", source: "n4", target: "n5", label: "jev result" },
    { id: "e6", source: "n5", target: "n6", label: "grace period" },
    { id: "e7", source: "n6", target: "n7", label: "late answer?" },
    { id: "e8", source: "n7", target: "n8", label: "merge & return" },
  ],
};
```

**Result:** Full decision dict with `record_id`, `local` (Kev answer), `alias_version`, `registry_hash`

---

## 3. Log Action Result

### `POST /log`

```typescript
const logSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
  ],
  nodes: [
    { id: "n1", label: "Receive record_id + result", kind: "start", lane: "api" },
    { id: "n2", label: "Merge result into record", kind: "step", lane: "store" },
    { id: "n3", label: "Record missed word as proposal", kind: "step", lane: "api" },
    { id: "n4", label: "Count shadow words", kind: "step", lane: "store" },
    { id: "n5", label: "Reload house if shadow changed", kind: "step", lane: "api" },
    { id: "n6", label: "Return 204 No Content", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "record + result" },
    { id: "e2", source: "n2", target: "n3", label: "house words exist?" },
    { id: "e3", source: "n2", target: "n4", label: "no house words" },
    { id: "e4", source: "n4", target: "n5", label: "shadow count >= 20?" },
    { id: "e5", source: "n5", target: "n6", label: "reload triggered" },
    { id: "e6", source: "n3", target: "n6", label: "skip reload" },
    { id: "e7", source: "n5", target: "n6", label: "no reload" },
  ],
};
```

**Result:** `204 No Content` (success) or `404` (unknown record)

---

## 4. Get Stats

### `GET /stats`

```typescript
const statsSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
    { id: "house", label: "House Words" },
  ],
  nodes: [
    { id: "n1", label: "GET /stats request", kind: "start", lane: "api" },
    { id: "n2", label: "Retrieve all records", kind: "step", lane: "store" },
    { id: "n3", label: "Get entity names from house", kind: "step", lane: "house" },
    { id: "n4", label: "Generate stats summary", kind: "step", lane: "api" },
    { id: "n5", label: "Map entity IDs to names", kind: "step", lane: "api" },
    { id: "n6", label: "Return dashboard JSON", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "request" },
    { id: "e2", source: "n2", target: "n3", label: "records needed" },
    { id: "e3", source: "n3", target: "n4", label: "entity map" },
    { id: "e4", source: "n4", target: "n5", label: "name mapping" },
    { id: "e5", source: "n5", target: "n6", label: "summary built" },
  ],
};
```

**Result:** Dashboard summary with totals, distributions, agreement stats

---

## 5. Get Record

### `GET /records/{record_id}`

```typescript
const recordSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
    { id: "house", label: "House Words" },
  ],
  nodes: [
    { id: "n1", label: "GET /records/{id} request", kind: "start", lane: "api" },
    { id: "n2", label: "Look up record by ID", kind: "step", lane: "store" },
    { id: "n3", label: "Record not found → 404", kind: "step", lane: "store" },
    { id: "n4", label: "Get entity names from house", kind: "step", lane: "house" },
    { id: "n5", label: "Extract mentioned entities", kind: "step", lane: "api" },
    { id: "n6", label: "Return record + names", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "record_id" },
    { id: "e2", source: "n2", target: "n3", label: "found?" },
    { id: "e3", source: "n2", target: "n4", label: "found → names" },
    { id: "e4", source: "n4", target: "n5", label: "record has entities" },
    { id: "e5", source: "n5", target: "n6", label: "named entities" },
    { id: "e6", source: "n3", target: "n6", label: "→ 404 response" },
  ],
};
```

**Result:** `{"record": {full decision}, "names": {"entity_id": "name", ...}}`

---

## 6. Health Check

### `GET /health`

```typescript
const healthSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "ha", label: "Home Assistant" },
    { id: "api", label: "API" },
  ],
  nodes: [
    { id: "n1", label: "GET /health request", kind: "start", lane: "ha" },
    { id: "n2", label: "Check if sync exists", kind: "step", lane: "api" },
    { id: "n3", label: "Return status JSON", kind: "end", lane: "ha" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "check sync" },
    { id: "e2", source: "n2", target: "n3", label: "synced?" },
  ],
};
```

**Result:** `{"status": "ok", "synced": true/false}`

*Note: No authentication token required.*

---

## 7. Notifications

### `GET /notifications`

```typescript
const notificationsSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
  ],
  nodes: [
    { id: "n1", label: "GET /notifications request", kind: "start", lane: "api" },
    { id: "n2", label: "Get current time", kind: "step", lane: "api" },
    { id: "n3", label: "Query store for due items", kind: "step", lane: "store" },
    { id: "n4", label: "Return items list", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "request" },
    { id: "e2", source: "n2", target: "n3", label: "time needed" },
    { id: "e3", source: "n3", target: "n4", label: "due items found" },
  ],
};
```

**Result:** `{"items": [notification1, notification2, ...]}`

---

## 8. Proposals

### `GET /proposals`

```typescript
const proposalsSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
  ],
  nodes: [
    { id: "n1", label: "GET /proposals request", kind: "start", lane: "api" },
    { id: "n2", label: "List proposed words", kind: "step", lane: "store" },
    { id: "n3", label: "Count refused proposals", kind: "step", lane: "store" },
    { id: "n4", label: "Return proposals + refused count", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "request" },
    { id: "e2", source: "n2", target: "n3", label: "proposals needed" },
    { id: "e3", source: "n3", target: "n4", label: "count ready" },
  ],
};
```

**Result:** `{"proposals": [...], "refused": N}`

---

### POST /proposal - Confirm/Refuse

```typescript
const proposalActionSpec: DiagramSpec = {
  version: 1,
  direction: "DOWN",
  lanes: [
    { id: "api", label: "API" },
    { id: "store", label: "SQLite Store" },
    { id: "house", label: "House Words" },
  ],
  nodes: [
    { id: "n1", label: "POST /proposal with id + decision", kind: "start", lane: "api" },
    { id: "n2", label: "Look up proposal by ID", kind: "step", lane: "store" },
    { id: "n3", label: "Validate still proposed", kind: "step", lane: "store" },
    { id: "n4", label: "Mark as confirmed/refused", kind: "step", lane: "store" },
    { id: "n5", label: "Add shadow word (if confirm)", kind: "step", lane: "house" },
    { id: "n6", label: "Record word change (if confirm)", kind: "step", lane: "store" },
    { id: "n7", label: "Reload house (if confirm)", kind: "step", lane: "api" },
    { id: "n8", label: "Return status", kind: "end", lane: "api" },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", label: "id + decision" },
    { id: "e2", source: "n2", target: "n3", label: "proposal exists?" },
    { id: "e3", source: "n3", target: "n4", label: "valid → update" },
    { id: "e4", source: "n4", target: "n5", label: "confirm → house" },
    { id: "e5", source: "n4", target: "n6", label: "confirm → store" },
    { id: "e6", source: "n5", target: "n7", label: "reload under lock" },
    { id: "e7", source: "n6", target: "n7", label: "always reload" },
    { id: "e8", source: "n7", target: "n8", label: "status returned" },
  ],
};
```

**Result:** `{"status": "confirmed"}` or `{"status": "refused"}`

---

## 9. Summary

| Endpoint | Diagram | Key Result |
| --- | --- | --- |
| `POST /sync` | [Sync Spec](#1-sync-house) | `{"entities": N, "alias_version": "v"}` |
| `POST /route` | [Route Spec](#2-route-command) | Full decision + `record_id` |
| `POST /log` | [Log Spec](#3-log-action-result) | `204 No Content` |
| `GET /stats` | [Stats Spec](#4-get-stats) | Aggregated dashboard stats |
| `GET /records/{id}` | [Record Spec](#5-get-record) | Record + named entities |
| `GET /health` | [Health Spec](#6-health-check) | `{"status": "ok", "synced": ...}` |
| `GET /notifications` | [Notifications Spec](#7-notifications) | `{"items": [...]}` |
| `GET /proposals` | [Proposals Spec](#8-proposals) | `{"proposals": [...], "refused": N}` |
| `POST /proposal` | [Proposal Action](#8-proposals) | `{"status": "confirmed"|"refused"}` |

---

*All diagrams use the `archify-react` library spec format. The diagrams visualize the complete request flow, including all internal steps, conditional branches, and data flow between the API, SQLite store, house words, and external models (Jev/OpenRouter, Kev local).*

*All endpoints require bearer token authentication except `GET /health`.*