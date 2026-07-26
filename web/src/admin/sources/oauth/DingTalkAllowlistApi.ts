import type { DingTalkAllowlistModel } from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import {
    type Configuration,
    DingTalkAllowlistApplyRequestRequest,
    DingTalkAllowlistRemoveRequestRequest,
    DingTalkAllowlistStatusResponse,
    SourcesApi,
} from "@goauthentik/api";

export interface DingTalkAllowlistStatus extends DingTalkAllowlistStatusResponse {
    revision: string;
}

export interface DingTalkAllowlistApplyRequest extends DingTalkAllowlistApplyRequestRequest {}

export interface DingTalkAllowlistRemoveRequest extends DingTalkAllowlistRemoveRequestRequest {}

function dingTalkAllowlistConfigToJSON(model: DingTalkAllowlistModel): unknown {
    return {
        companies: model.companies.map((company) => ({
            corp_id: company.corpId,
            label: company.label,
            allow_all: company.allowAll,
            dept_ids: company.deptIds.map(String),
        })),
    };
}

export class DingTalkAllowlistApi {
    private readonly api: SourcesApi;

    public constructor(configuration?: Configuration) {
        this.api = new SourcesApi(configuration);
    }

    public async sourcesOauthDingtalkAllowlistApplyCreate(
        sourceSlug: string,
        request: DingTalkAllowlistApplyRequest,
    ): Promise<DingTalkAllowlistStatus> {
        return this.api.sourcesOauthDingtalkAllowlistApplyCreate({
            sourceSlug,
            dingTalkAllowlistApplyRequestRequest: {
                config: dingTalkAllowlistConfigToJSON(request.config as DingTalkAllowlistModel),
                expectedRevision: request.expectedRevision,
            },
        });
    }

    public async sourcesOauthDingtalkAllowlistRemoveCreate(
        sourceSlug: string,
        request: DingTalkAllowlistRemoveRequest,
    ): Promise<DingTalkAllowlistStatus> {
        return this.api.sourcesOauthDingtalkAllowlistRemoveCreate({
            sourceSlug,
            dingTalkAllowlistRemoveRequestRequest: {
                expectedRevision: request.expectedRevision,
            },
        });
    }

    public async sourcesOauthDingtalkAllowlistStatusRetrieve(requestParameters: {
        sourceSlug: string;
    }): Promise<DingTalkAllowlistStatus> {
        return this.api.sourcesOauthDingtalkAllowlistStatusRetrieve(requestParameters);
    }
}
