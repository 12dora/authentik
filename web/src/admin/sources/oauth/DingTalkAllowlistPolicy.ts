import { msg, str } from "@lit/localize";

export const DINGTALK_ALLOWLIST_MARKER = "authentik-managed-dingtalk-allowlist";

export interface DingTalkAllowlistCompany {
    corpId: string;
    label: string;
    allowAll: boolean;
    deptIds: Array<string | number>;
}

export interface DingTalkAllowlistModel {
    companies: DingTalkAllowlistCompany[];
}

interface StoredDingTalkAllowlistCompany {
    corp_id: string;
    label?: string;
    name?: string;
    allow_all: boolean;
    dept_ids?: string[];
    dept_id_list?: string[];
}

interface StoredDingTalkAllowlistModel {
    companies: StoredDingTalkAllowlistCompany[];
}

function normalizeString(value: string | number | undefined | null): string {
    if (value === undefined || value === null) {
        return "";
    }
    return String(value).trim();
}

// The serialized config doubles as the backend's `config_version` session-marker
// comparison value; ordering must match Python's `sorted()` (plain code-point order),
// not any locale- or numeric-aware collation.
function sortStrings(values: string[]): string[] {
    return [...values].sort();
}

function toStoredModel(model: DingTalkAllowlistModel): StoredDingTalkAllowlistModel {
    return {
        companies: model.companies.map((company) => ({
            allow_all: company.allowAll,
            corp_id: company.corpId,
            dept_ids: company.deptIds.map((deptId) => String(deptId)),
            label: company.label,
        })),
    };
}

function fromStoredModel(model: StoredDingTalkAllowlistModel): DingTalkAllowlistModel {
    return {
        companies: model.companies.map((company) => ({
            corpId: company.corp_id,
            label: company.label ?? company.name ?? "",
            allowAll: company.allow_all,
            deptIds: company.dept_ids ?? company.dept_id_list ?? [],
        })),
    };
}

export function validateDingTalkAllowlistModel(
    model: DingTalkAllowlistModel,
): DingTalkAllowlistModel {
    if (!Array.isArray(model.companies) || model.companies.length < 1) {
        throw new Error(
            msg("Add at least one DingTalk company.", {
                id: "sources.oauth.dingtalk-allowlist.validation.company-required",
            }),
        );
    }

    const corpIds = new Set<string>();

    const companies = model.companies.map((company) => {
        const corpId = normalizeString(company.corpId);
        if (!corpId) {
            throw new Error(
                msg("Company corpId is required.", {
                    id: "sources.oauth.dingtalk-allowlist.validation.corp-id-required",
                }),
            );
        }
        if (corpIds.has(corpId)) {
            throw new Error(
                msg(str`Duplicate company corpId: ${corpId}.`, {
                    id: "sources.oauth.dingtalk-allowlist.validation.duplicate-corp-id",
                }),
            );
        }
        corpIds.add(corpId);

        const deptIds = sortStrings(
            Array.from(
                new Set(
                    company.deptIds
                        .map((deptId) => normalizeString(deptId))
                        .filter((deptId) => deptId.length > 0),
                ),
            ),
        );
        const allowAll = Boolean(company.allowAll);

        if (!allowAll && deptIds.length < 1) {
            throw new Error(
                msg(
                    str`Company ${corpId} must allow all users or include at least one department.`,
                    {
                        id: "sources.oauth.dingtalk-allowlist.validation.departments-required",
                    },
                ),
            );
        }

        return {
            corpId,
            label: normalizeString(company.label),
            allowAll,
            deptIds: allowAll ? [] : deptIds,
        };
    });

    return {
        // Plain code-point order keeps the serialized config (and therefore the
        // backend's config_version comparison) deterministic across environments.
        companies: companies.sort((left, right) =>
            left.corpId < right.corpId ? -1 : left.corpId > right.corpId ? 1 : 0,
        ),
    };
}

export function dingTalkAllowlistModelFromStoredConfig(
    value: unknown,
): DingTalkAllowlistModel | null {
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Partial<StoredDingTalkAllowlistModel>;
    if (!Array.isArray(record.companies)) {
        return null;
    }
    try {
        return validateDingTalkAllowlistModel(
            fromStoredModel(record as StoredDingTalkAllowlistModel),
        );
    } catch {
        return null;
    }
}

function pythonString(value: string): string {
    return JSON.stringify(value);
}

function pythonBool(value: boolean): string {
    return value ? "True" : "False";
}

function pythonSet(values: string[]): string {
    if (values.length < 1) {
        return "set()";
    }
    return `{${values.map((value) => pythonString(value)).join(", ")}}`;
}

function renderPythonAllowlist(model: DingTalkAllowlistModel): string {
    return `allowlist = {\n${model.companies
        .map(
            (company) =>
                `    ${pythonString(company.corpId)}: {"allow_all": ${pythonBool(
                    company.allowAll,
                )}, "dept_ids": ${pythonSet(company.deptIds.map((deptId) => String(deptId)))}}`,
        )
        .join(",\n")}\n}`;
}

export function renderDingTalkAllowlistPolicy(
    model: DingTalkAllowlistModel,
    sourceSlug: string,
): string {
    const normalized = validateDingTalkAllowlistModel(model);
    const storedModel = toStoredModel(normalized);
    const configVersion = JSON.stringify(storedModel);

    return `# ${DINGTALK_ALLOWLIST_MARKER}
# config: ${configVersion}

source = context.get("source")
if source and getattr(source, "slug", None) != ${pythonString(sourceSlug)}:
    return True

info = context.get("oauth_userinfo", {})
prompt_data = context.get("prompt_data", {})
dingtalk_attrs = prompt_data.get("attributes", {}).get("dingtalk", {})
if not dingtalk_attrs:
    dingtalk_attrs = (
        request.context.get("prompt_data", {})
        .get("attributes", {})
        .get("dingtalk", {})
    )

corp_id = (
    info.get("corpId")
    or info.get("corp_id")
    or dingtalk_attrs.get("corp_id")
)

raw_dept_ids = info.get("dept_id_list") or dingtalk_attrs.get("dept_id_list")
dept_ids = (
    {str(dept_id) for dept_id in raw_dept_ids if dept_id is not None}
    if isinstance(raw_dept_ids, (list, tuple, set))
    else None
)

if not corp_id:
    if request.obj.__class__.__name__ != "Application":
        return True
    marker = context.get("authentik/sources/oauth/dingtalk/allowlist") or {}
    if not marker:
        ak_message("钉钉登录失败：请通过允许的钉钉组织登录后访问此应用。")
        return False
    if marker.get("config_version") != ${pythonString(configVersion)}:
        ak_message("钉钉登录失败：当前白名单状态已更新，请重新通过钉钉登录。")
        return False
    corp_id = marker.get("corp_id")
    raw_dept_ids = marker.get("dept_ids")
    dept_ids = (
        {str(dept_id) for dept_id in raw_dept_ids if dept_id is not None}
        if isinstance(raw_dept_ids, (list, tuple, set))
        else None
    )

${renderPythonAllowlist(normalized)}

if not corp_id:
    ak_message("钉钉登录失败：无法确认企业信息，请联系管理员。")
    return False

rule = allowlist.get(corp_id)
if not rule:
    ak_message("钉钉登录失败：当前企业未被允许，请联系管理员。")
    return False

if rule.get("allow_all"):
    return True

if dept_ids is None:
    ak_message("钉钉登录失败：无法确认部门信息，请联系管理员。")
    return False

allowed_dept_ids = {str(dept_id) for dept_id in rule.get("dept_ids") or set()}
if dept_ids & allowed_dept_ids:
    return True

ak_message("钉钉登录失败：当前部门未被允许，请联系管理员。")
return False
`;
}

export function hasDingTalkAllowlistPolicyMarker(expression: string): boolean {
    return expression.split("\n").some((line) => line.trim() === `# ${DINGTALK_ALLOWLIST_MARKER}`);
}

export function parseDingTalkAllowlistPolicy(expression: string): DingTalkAllowlistModel | null {
    if (!hasDingTalkAllowlistPolicyMarker(expression)) {
        return null;
    }

    const lines = expression.split("\n");
    const configLine = lines.find((line) => line.startsWith("# config: "));
    if (!configLine) {
        return null;
    }

    try {
        return dingTalkAllowlistModelFromStoredConfig(
            JSON.parse(configLine.slice("# config: ".length)),
        );
    } catch {
        return null;
    }
}
