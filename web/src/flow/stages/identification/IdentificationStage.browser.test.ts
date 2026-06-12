import zhHansMessages from "../../../../xliff/zh-Hans.xlf?raw";
import identificationStageSource from "./IdentificationStage?raw";

import { describe, expect, it } from "vitest";

describe("IdentificationStage labels", () => {
    it("uses a stable message id for the email or username label", () => {
        expect(identificationStageSource).toContain('msg("Email or username"');
        expect(identificationStageSource).toContain('id: "flow.identification.email-or-username"');
        expect(identificationStageSource).not.toContain("EMAIL_OR_USERNAME_LABEL");
        expect(zhHansMessages).toContain("<target>邮箱或用户名</target>");
    });

    it("localizes the Chinese login prelude and source button labels", () => {
        expect(zhHansMessages).toContain("<target>登录以继续访问");
        expect(zhHansMessages).toContain("继续使用");
    });
});
