import { LocaleContextController } from "#elements/controllers/LocaleContextController";

import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCALE_STATUS_EVENT } from "@lit/localize";

const UserFieldsEnum = {
    Email: "email",
    Upn: "upn",
    Username: "username",
} as const;

type UserFieldsEnum = (typeof UserFieldsEnum)[keyof typeof UserFieldsEnum];

interface IdentificationChallengeFixture {
    component: string;
    flowInfo: {
        title: string;
        background: string;
        cancelUrl: string;
    };
    primaryAction: string;
    userFields: UserFieldsEnum[];
}

interface IdentificationStageElement extends HTMLElement {
    activeLanguageTag: string;
    challenge: IdentificationChallengeFixture;
    host: {
        submit: () => Promise<boolean>;
    };
    updateComplete: Promise<unknown>;
}

let identificationFieldLabel: (fields: UserFieldsEnum[]) => string;

vi.mock("@goauthentik/api", () => ({
    CapabilitiesEnum: {
        CanDebug: "can_debug",
    },
    ConfigFromJSON: (value: unknown) => value,
    CurrentBrandFromJSON: (value: unknown) => value,
    FlowDesignationEnum: {
        Authentication: "authentication",
        Enrollment: "enrollment",
        Invalidation: "invalidation",
        Recovery: "recovery",
        StageConfiguration: "stage_configuration",
        Unenrollment: "unenrollment",
    },
    FlowLayoutEnum: {
        ContentLeft: "content_left",
        ContentRight: "content_right",
        Stacked: "stacked",
    },
    LoginChallengeTypes: {
        Password: "password",
        Redirect: "redirect",
        Source: "source",
        WebAuthn: "webauthn",
    },
    UiThemeEnum: {
        Automatic: "automatic",
        Dark: "dark",
        Light: "light",
    },
    UserFieldsEnum,
}));

vi.mock("#elements/sources/utils", () => ({
    renderSourceIcon: () => "",
}));

vi.mock("#common/errors/network", () => ({
    parseAPIResponseError: async (error: unknown) => error,
    pluckErrorDetail: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));

vi.mock("#flow/stages/captcha/CaptchaStage", () => ({}));

function setAuthentikGlobal(): void {
    (
        window as unknown as {
            authentik: unknown;
        }
    ).authentik = {
        brand: {
            matched_domain: "",
            branding_title: "authentik",
            branding_logo: "",
            branding_logo_themed_urls: {},
            branding_favicon: "",
            branding_favicon_themed_urls: {},
            branding_custom_css: "",
            ui_footer_links: [],
            ui_theme: "automatic",
            default_locale: "en",
            flags: {
                core_default_app_access: false,
                enterprise_audit_include_expanded_diff: false,
                flows_continuous_login: false,
                flows_refresh_others: false,
            },
        },
        api: {
            base: "/",
            relBase: "/",
        },
        config: {
            error_reporting: {
                enabled: false,
                sentry_dsn: "",
                environment: "",
                send_pii: false,
                traces_sample_rate: 0,
            },
            capabilities: [],
            cache_timeout: 0,
            cache_timeout_flows: 0,
            cache_timeout_policies: 0,
        },
        locale: "en",
        versionFamily: "",
        versionSubdomain: "",
        build: "",
    };
}

function makeChallenge(userFields: UserFieldsEnum[]): IdentificationChallengeFixture {
    return {
        component: "ak-stage-identification",
        flowInfo: {
            title: "Sign in",
            background: "",
            cancelUrl: "",
        },
        primaryAction: "Continue",
        userFields,
    };
}

function waitForLocale(languageTag: string): Promise<void> {
    return new Promise((resolve) => {
        if (document.documentElement.lang === languageTag) {
            resolve();
            return;
        }

        const listener = (event: CustomEvent) => {
            if (event.detail.status !== "ready" || event.detail.readyLocale !== languageTag) {
                return;
            }

            window.removeEventListener(LOCALE_STATUS_EVENT, listener as EventListener);
            resolve();
        };

        window.addEventListener(LOCALE_STATUS_EVENT, listener as EventListener);
    });
}

describe("identificationFieldLabel", () => {
    beforeAll(async () => {
        ({ identificationFieldLabel } =
            await import("#flow/stages/identification/IdentificationStage"));
    });

    beforeEach(setAuthentikGlobal);

    it("returns one stable label for the email and username pair", () => {
        expect(identificationFieldLabel([UserFieldsEnum.Email, UserFieldsEnum.Username])).toBe(
            "Email or username",
        );
    });

    it("humanizes unknown identification fields instead of leaking enum tokens", () => {
        expect(identificationFieldLabel(["phone-number" as UserFieldsEnum])).toBe("Phone Number");
    });
});

describe("IdentificationStage labels", () => {
    beforeAll(async () => {
        await import("#flow/stages/identification/IdentificationStage");
    });

    beforeEach(setAuthentikGlobal);

    it("renders the zh-Hans email or username label as visible and programmatic text", async () => {
        const stage = document.createElement(
            "ak-stage-identification",
        ) as IdentificationStageElement;
        const localeReady = waitForLocale("zh-Hans");
        const localeController = new LocaleContextController(stage, "zh-Hans");

        stage.host = {
            submit: async () => true,
        };
        stage.challenge = makeChallenge([UserFieldsEnum.Email, UserFieldsEnum.Username]);

        document.body.append(stage);
        await localeReady;
        stage.requestUpdate();
        await stage.updateComplete;
        await stage.updateComplete;

        const label = stage.shadowRoot?.querySelector("label");
        const input = stage.shadowRoot?.querySelector<HTMLInputElement>("input[name=uidField]");

        expect(label?.textContent?.trim()).toBe("邮箱或用户名");
        expect(input?.placeholder).toBe("邮箱或用户名");
        expect(input?.id).toBe(label?.getAttribute("for"));
        expect(localeController.activeLanguageTag).toBe("zh-Hans");

        stage.remove();
        stage.activeLanguageTag = "en";
    });
});
