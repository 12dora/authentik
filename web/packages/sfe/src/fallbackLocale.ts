export const FALLBACK_MESSAGES = {
    identifier: {
        default: "Email / Username",
        zhHans: "邮箱或用户名",
    },
    password: {
        default: "Password",
        zhHans: "密码",
    },
    loading: {
        default: "Loading...",
        zhHans: "加载中...",
    },
    continue: {
        default: "Continue",
        zhHans: "继续",
    },
    loginPrelude: {
        default: "Log in to continue to {0}.",
        zhHans: "登录以继续访问 {0}。",
    },
    welcome: {
        default: "Welcome, {0}.",
        zhHans: "欢迎，{0}。",
    },
    selectAuthMethod: {
        default: "Select an authentication method.",
        zhHans: "选择身份验证方法。",
    },
    noCompatibleAuthMethod: {
        default: "No compatible authentication method available",
        zhHans: "没有可用的兼容身份验证方法",
    },
    recoveryKeys: {
        default: "Recovery keys",
        zhHans: "恢复密钥",
    },
    traditionalAuthenticator: {
        default: "Traditional authenticator",
        zhHans: "传统身份验证器",
    },
    securityKey: {
        default: "Security key",
        zhHans: "安全密钥",
    },
    enterCode: {
        default: "Please enter your code",
        zhHans: "请输入您的代码",
    },
    accessDenied: {
        default: "Access denied.",
        zhHans: "访问被拒绝。",
    },
    unsupportedStage: {
        default: "Unsupported stage: {0}",
        zhHans: "不支持的阶段：{0}",
    },
};

export type FallbackMessageKey = keyof typeof FALLBACK_MESSAGES;

export function shouldUseSimplifiedChineseFallback(language: string): boolean {
    const tag = language.toLowerCase();

    if (!tag.startsWith("zh")) {
        return false;
    }

    if (/[-_]hant\b/.test(tag) || /[-_](tw|hk|mo)\b/.test(tag)) {
        return false;
    }

    return true;
}

export function fallbackMessageForLanguage(
    language: string,
    key: FallbackMessageKey,
    ...args: string[]
): string {
    const messages = FALLBACK_MESSAGES[key];
    const template = shouldUseSimplifiedChineseFallback(language)
        ? messages.zhHans
        : messages.default;

    return template.replace(/\{(\d+)\}/g, (_match, index: string) => args[Number(index)] ?? "");
}
