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

function sortStrings(values: string[]): string[] {
    return [...values].sort((left, right) =>
        left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" }),
    );
}

function toStoredModel(model: DingTalkAllowlistModel): StoredDingTalkAllowlistModel {
    return {
        companies: model.companies.map((company) => ({
            corp_id: company.corpId,
            label: company.label,
            allow_all: company.allowAll,
            dept_ids: company.deptIds.map((deptId) => String(deptId)),
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
        throw new Error("Add at least one DingTalk company.");
    }

    const corpIds = new Set<string>();

    const companies = model.companies.map((company) => {
        const corpId = normalizeString(company.corpId);
        if (!corpId) {
            throw new Error("Company corpId is required.");
        }
        if (corpIds.has(corpId)) {
            throw new Error(`Duplicate company corpId: ${corpId}.`);
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
                `Company ${corpId} must allow all users or include at least one department.`,
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
        companies: companies.sort((left, right) =>
            left.corpId.localeCompare(right.corpId, undefined, {
                numeric: true,
                sensitivity: "base",
            }),
        ),
    };
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

    return `# ${DINGTALK_ALLOWLIST_MARKER}
# config: ${JSON.stringify(storedModel)}

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

export function parseDingTalkAllowlistPolicy(expression: string): DingTalkAllowlistModel | null {
    const lines = expression.split("\n");
    if (!lines.some((line) => line.trim() === `# ${DINGTALK_ALLOWLIST_MARKER}`)) {
        return null;
    }

    const configLine = lines.find((line) => line.startsWith("# config: "));
    if (!configLine) {
        throw new Error("Managed DingTalk allowlist policy is missing its config block.");
    }

    const parsed = JSON.parse(
        configLine.slice("# config: ".length),
    ) as StoredDingTalkAllowlistModel;
    return validateDingTalkAllowlistModel(fromStoredModel(parsed));
}
