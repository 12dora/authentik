import { aki } from "#common/api/client";

import {
    type DingTalkDirectoryStatus,
    type DingTalkDirectorySyncQueued,
    type DingTalkDirectorySyncRequestRequest,
    SourcesApi,
} from "@goauthentik/api";

export interface DingTalkDirectoryClient {
    status(sourceSlug: string): Promise<DingTalkDirectoryStatus>;
    sync(
        sourceSlug: string,
        request: DingTalkDirectorySyncRequestRequest,
    ): Promise<DingTalkDirectorySyncQueued>;
    destroy(sourceSlug: string, corpId: string): Promise<void>;
}

export class GeneratedDingTalkDirectoryClient implements DingTalkDirectoryClient {
    constructor(private readonly api: SourcesApi = aki(SourcesApi)) {}

    status(sourceSlug: string): Promise<DingTalkDirectoryStatus> {
        return this.api.sourcesOauthDingtalkDirectoryStatusRetrieve({ sourceSlug });
    }

    sync(
        sourceSlug: string,
        request: DingTalkDirectorySyncRequestRequest,
    ): Promise<DingTalkDirectorySyncQueued> {
        return this.api.sourcesOauthDingtalkDirectorySyncCreate({
            sourceSlug,
            dingTalkDirectorySyncRequestRequest: request,
        });
    }

    async destroy(sourceSlug: string, corpId: string): Promise<void> {
        await this.api.sourcesOauthDingtalkDirectorySyncDestroy({ sourceSlug, corpId });
    }
}
