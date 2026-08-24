import type { DingTalkAllowlistModel } from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import {
    type DingTalkAllowlistApplyRequestRequest,
    type DingTalkAllowlistRemoveRequestRequest,
    type DingTalkAllowlistStatusResponse,
    SourcesApi,
} from "@goauthentik/api";

export interface DingTalkAllowlistStatus extends DingTalkAllowlistStatusResponse {
    revision: string;
}

export type DingTalkAllowlistApplyRequest = DingTalkAllowlistApplyRequestRequest;

export type DingTalkAllowlistRemoveRequest = DingTalkAllowlistRemoveRequestRequest;

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

    // The caller injects the endpoint (`aki(SourcesApi)` in the panel) rather than
    // this module importing `#common/api/client` itself: that module pulls in the
    // API middleware, which reaches `AKElement` and therefore `CSSStyleSheet`, and
    // would break the Node-environment unit tests that import this file directly.
    public constructor(api: SourcesApi) {
        this.api = api;
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
