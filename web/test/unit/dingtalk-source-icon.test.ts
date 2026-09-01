import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const identificationStyles = readFileSync(
    resolve(import.meta.dirname, "../../src/flow/stages/identification/styles.css"),
    "utf8",
);
const userSourceSettings = readFileSync(
    resolve(import.meta.dirname, "../../src/elements/user/sources/SourceSettings.css"),
    "utf8",
);
const typeCreateWizard = readFileSync(
    resolve(import.meta.dirname, "../../src/elements/wizard/TypeCreateWizardPage.ts"),
    "utf8",
);

describe("DingTalk source icon dark theme", () => {
    it.each([
        ["identification stage", identificationStyles],
        ["user source settings", userSourceSettings],
        ["type create wizard", typeCreateWizard],
    ])("keeps the DingTalk mark blue on %s", (_label, source) => {
        expect(source).toContain('img[src*="dingtalk"]');
        expect(source).toMatch(/img\[src\*="dingtalk"\][\s\S]{0,80}filter:\s*none/);
    });
});
