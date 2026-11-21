import { beforeEach } from "vitest";
import { invalidatePageSnapshots } from "./lib/page-snapshots";

// Each test models a fresh browser session; navigation inside a test retains data.
beforeEach(invalidatePageSnapshots);
