import { describe, it } from "vitest";

// Phase 3 acceptance list (plan "Frontend" and "Main states"). Each entry becomes a real test
// in the same PR that builds the component; todo entries show up in the report as pending.

describe("Upload", () => {
  it.todo("offers a camera input with capture=environment and a gallery pick");
  it.todo("rejects files that are not JPEG, PNG or WebP before sending");
  it.todo("rejects files over 10 MB with a readable message");
  it.todo("posts the file as multipart field 'image' and shows per-stage loading text");
  it.todo("ignores a second upload while one is in flight");
});

describe("Canvas overlay", () => {
  it.todo("draws box masks green when intact and red with a hatch pattern when damaged");
  it.todo("draws walkway masks yellow dashed and blocked overlaps red hatched");
  it.todo("pairs every colour with an icon and a text label");
  it.todo("honours prefers-reduced-motion by disabling the pulse");
  it.todo("scales mask coordinates when the image is shown at a different size");
});

describe("Findings list", () => {
  it.todo("renders severity badge and confidence for each finding");
  it.todo("shows a needs-review badge with its reason for low-confidence findings");
  it.todo("greys out rejected findings, keeps the reason visible and excludes them from dispatch");
  it.todo("disables Approve & Dispatch when nothing is selected");
  it.todo("disables Approve & Dispatch while the request is pending (double click sends one request)");
});

describe("Agent timeline", () => {
  it.todo("lists tool name, argument summary and result for every action");
  it.todo("shows a guard-rejected action with its rejection reason");
  it.todo("links ticket PDFs to /tickets/{id}.pdf");
});

describe("Main states", () => {
  it.todo("shows 'no freight found' when nothing is detected");
  it.todo("shows a friendly retry message on 502 and 504");
  it.todo("shows a visible replay banner when DEMO_MODE=replay results are served");
  it.todo("flags 'people blur failed, crops hidden'");
  it.todo("is a single column on narrow screens");
});

describe("KPI strip", () => {
  it.todo("shows pipeline agreement and agent score with their sample sizes");
  it.todo("shows 'n/a (n so far)' below the minimum review count");
  it.todo("shows the open-trace link only when tracing is configured");
});
