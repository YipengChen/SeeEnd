# Historical 0.8.4 proprietary build validation

This records the initial 2026-10-09 release before AI Flame removal. The standalone esptool checks below apply only to that earlier artifact. Current local builds exclude it; see [maintenance audit](../../MAINTENANCE_AUDIT.md). Public release assets are not updated by local builds.

Original source repository remains private. Original code is proprietary from 0.8.4; existing grants are retained. Public repository uploads use a document whitelist.

55 browser-host, desktop-services, desktop-app and standalone-tool tests passed. The final frozen lifecycle test passed. The separate esptool executable reports 5.4.0 and contains no SeeEnd modules. The desktop module archive contains no esptool/readline, and readline is absent from bundled dynamic libraries.

A native-message ping using the store origin miokmdeomlchjpihkcnommpnheagoimc succeeded with the final frozen executable; an unknown origin was rejected. This is native-host verification, not an assertion that the store extension has already been installed or approved.

Actual bundled conda dynamic-library providers were checked against third-party materials. Qt/PySide, LGPL library sources, distributor build recipes and patches are supplied with license texts and library replacement instructions. Third-party source archive inspection found no SeeEnd source paths. Final archive source hashes match recorded upstream hashes.

macOS Apple Silicon only. Ad-hoc signed, not Apple-notarized. Other platform binaries are not supplied.
