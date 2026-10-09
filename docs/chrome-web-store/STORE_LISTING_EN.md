# SeeEnd English store listing

## Name

SeeEnd · AI Web Chat Status

## Short description

Send AI web chat generation and completion status to the local SeeEnd desktop app. Does not read prompts or response text.

## Detailed description

SeeEnd connects AI web chat status to your local SeeEnd desktop app, so you can follow task progress through your configured reminders.

Requires the SeeEnd desktop app to be installed, running, and connected through its native messaging bridge. This extension does not install the desktop app or independently control lights. Light reminders require a configured supported device.

After you submit a new text message, the extension observes generation controls and explicit completion controls. It sends status events to the local desktop app. The popup shows page recognition, native bridge connection and the latest delivery result. Stopping generation, navigating away, refreshing or closing a page does not count as successful completion.

Setup: install and launch SeeEnd, open Application Integrations, click Connect Chrome, refresh the AI chat page, and start a new text conversation. Check the extension popup and SeeEnd activity log.

Privacy: supported-site addresses and control states are processed locally to identify the site, navigation and generation state. The extension does not read editor contents, prompts, response text or account credentials. It sends only status events to the local SeeEnd app, not developer-operated servers. Page URLs and site reply identifiers are not included in those events.

Website changes may affect detection. Image, voice, agent and deep-research modes have not been fully validated. Capture is not guaranteed during sleep, tab freezing/discarding or browser shutdown. Completion is never inferred from a timeout.

SeeEnd is an independent companion tool and is not affiliated with or endorsed by the supported AI platforms.
