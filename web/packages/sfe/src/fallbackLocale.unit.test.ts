import { fallbackMessageForLanguage, shouldUseSimplifiedChineseFallback } from "./fallbackLocale";

import { describe, expect, it } from "vitest";

describe("shouldUseSimplifiedChineseFallback", () => {
    it("returns true for Simplified Chinese language tags", () => {
        expect(shouldUseSimplifiedChineseFallback("zh")).toBe(true);
        expect(shouldUseSimplifiedChineseFallback("zh-Hans")).toBe(true);
        expect(shouldUseSimplifiedChineseFallback("zh-CN")).toBe(true);
        expect(shouldUseSimplifiedChineseFallback("zh-SG")).toBe(true);
    });

    it("returns false for Traditional Chinese language tags", () => {
        expect(shouldUseSimplifiedChineseFallback("zh-Hant")).toBe(false);
        expect(shouldUseSimplifiedChineseFallback("zh-TW")).toBe(false);
        expect(shouldUseSimplifiedChineseFallback("zh-HK")).toBe(false);
        expect(shouldUseSimplifiedChineseFallback("zh-MO")).toBe(false);
    });

    it("returns false for non-Chinese language tags", () => {
        expect(shouldUseSimplifiedChineseFallback("en-US")).toBe(false);
        expect(shouldUseSimplifiedChineseFallback("ja-JP")).toBe(false);
    });
});

describe("fallbackMessageForLanguage", () => {
    it("substitutes placeholders in Simplified Chinese messages", () => {
        expect(fallbackMessageForLanguage("zh-Hans", "loginPrelude", "EasyAuth")).toBe(
            "登录以继续访问 EasyAuth。",
        );
    });

    it("uses the default table for Traditional Chinese until a Traditional table exists", () => {
        expect(fallbackMessageForLanguage("zh-Hant", "loginPrelude", "EasyAuth")).toBe(
            "Log in to continue to EasyAuth.",
        );
    });
});
