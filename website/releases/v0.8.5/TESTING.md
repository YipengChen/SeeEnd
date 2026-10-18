# SeeEnd review test instructions

Desktop app download: https://github.com/YipengChen/SeeEnd-downloads/releases/tag/v0.8.5.
Published desktop baseline: SeeEnd 0.8.5 for macOS Apple Silicon. The submitted Web Store review artifact remains extension 0.2.8; this is a historical review combination. Current source extension 0.2.10 and App 0.8.6 are prepared separately and are not yet claimed as published. Windows and Intel Mac review builds are not included in this kit.
Reviewer contact: ypchenmail@gmail.com.

The desktop native host accepts this store item ID (miokmdeomlchjpihkcnommpnheagoimc) and the existing development ID. Public download availability must be checked before submission.

## Setup

1. Download the macOS Apple Silicon desktop build from the link above, unzip it and launch SeeEnd.app. If macOS blocks the download, follow the normal OS approval process; contact the developer if the supplied review build cannot launch.
2. Install the extension supplied through the Web Store review flow. Keep SeeEnd running. Open its Application Integrations page (应用接入), click Install browser extension (安装浏览器扩展), and use the Connect and verify websites step (连接并验证网站) to register the com.aiflame.chatgpt native messaging host on this computer. The supplied extension only needs to be installed once.
3. Open or refresh a supported AI chat website. The extension needs a new user-initiated chat after refresh; it does not retroactively track a generation already in progress.
4. Click the extension toolbar icon, then Check current page (检查当前页面). The popup should show the site, bridge connection and recognized page controls. Device offline is acceptable for status-only review.

## Functional test without hardware

1. Use ChatGPT at https://chatgpt.com/ with an account you are authorized to use if required by the site. Submit an ordinary text request, for example: “List 100 everyday objects, one per line.”
2. During generation, check the popup and SeeEnd Activity Log (活动记录) for a working event.
3. After the reply ends and its completion controls appear, check for a done event / completed reason and a delivery result. This tests real page detection, not the separate desktop bridge simulation button.
4. Repeat on https://gemini.google.com/ and https://claude.ai/ if accounts and regional access permit. Additional adapters include DeepSeek, Qianwen, Doubao, Kimi, Yuanbao and Zhipu.
5. Start a longer response, then click the website Stop control. The task should cancel / become idle and should not produce a done event for that cancelled generation.
6. Refresh or close a page during another generation. It should not become successful completion. Merely opening historical completed replies must not create a new done event.

No SeeEnd account or subscription is required. Supported AI websites may independently require accounts. No third-party account credentials are included in this kit; if the reviewer needs supplied credentials, the developer must provide a dedicated authorized test account through the dashboard's reviewer-only credentials field.

## Optional hardware check

Add and configure a supported device in My Devices (我的设备), then repeat a text task. Verify the configured device behavior separately. A bridge acknowledgement or event in the activity log does not prove hardware illumination.

## Technical and privacy notes

The extension bundles all JavaScript; it does not execute remotely hosted code. It sends only structured status events to the local desktop application. It does not read prompts, response text or credentials. Current supported-site URLs and control state are inspected locally. Qianwen and Zhipu include a bundled MAIN-world renderer scheduling helper for hidden tracked tasks; it adjusts pending rendering callbacks, not network traffic or chat contents.

A page redesign or special image, voice, agent or deep-research mode may not be recognized. Browser shutdown, sleep, tab discard and freezing can prevent capture. When uncertain, the extension does not infer success from elapsed time.

## Troubleshooting

Native messaging host not found: keep the desktop app running and reopen Install browser extension → Connect and verify websites; ensure the desktop build uses this store item's extension ID. Refresh the AI page afterwards.
Page controls not recognized: use a standard text-chat page, refresh it, and submit a new message.
Bridge connected but device offline: review the status-only workflow first; hardware is optional for this portion.
