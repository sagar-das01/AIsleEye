import { vi } from "vitest";

// jsdom has no object URLs; the dashboard uses them to preview the chosen photo.
let counter = 0;
URL.createObjectURL = vi.fn(() => `blob:test/${++counter}`);
URL.revokeObjectURL = vi.fn();
