# Android application 1.7.0

`AbreHamrahiAPI-1.7.0-android.apk` is a native Android 8.0+ application that connects directly to Abrehamrahi over HTTPS. It has no embedded account or Windows API connection.

## Use

1. Install the APK from Releases. Sign in with your own SMS code or password. Passwords/codes are not saved; sessions use Android Keystore encryption in app-private storage excluded from Android backups.
2. Select source folders through the system folder chooser. Only persistent read access is requested. Original names are preserved; duplicate root names are rejected.
3. Choose instant or daily scheduling for each folder. Schedule changes save immediately during a pass and apply to subsequent work. Daily deadlines persist; overdue jobs catch up and unsuccessful folders stay due.
4. Settings include remote root, optional size cap (zero means unlimited) and unmetered-network preference. The root defaults to the phone model and is fixed after indexing. There are no implicit name/type exclusions or 5,000-file cap.
5. Start synchronization or monitoring. The foreground notification reopens the app or stops work. Monitoring checks approximately every 30 seconds after a pass; persisted JobScheduler work has a minimum 15-minute interval and follows Android/network/battery policy.

Android's Storage Access Framework restricts some storage roots, Downloads, `Android/data`, `Android/obb` and other apps' private data. Only user-granted document trees are read. Force-stop prevents background work until reopening. Android foreground-service time limits may require restarting monitoring. Physical device behavior depends on the device/provider; exact background deadlines are not promised.

## Files and recovery

A changed indexed file is replaced in its original cloud path using native overwrite/version-group handling. No custom historical copy is uploaded. Unchanged content reuses the local index and current cloud metadata. Automatic passes can reuse known source size/time/URI fingerprints; manual sync rereads snapshots.

Two local reads must agree before upload. After completion, fresh metadata must match finalized key, ID, byte size, name and parent. Full SHA-256 download is used to reconcile uncertain completion or adopt an existing identical file, rather than downloading every successful upload again.

After successful transfers, the app rescans the selected source and moves absent cloud paths to native trash. Unreadable sources or failed file operations prevent that folder's cleanup. Trash targets are checked before and after the request; no permanent deletion is used. Restore through the cloud interface. Earlier custom history remains untouched.

The completion journal survives interruption. A lost response is checked against the current object and downloaded content before reuse. Pre-completion failure retries without uploading already confirmed content. Different remote content is a visible conflict.

Progress uses confirmed bytes across the pass, separate from the dashboard's whole-folder local/cloud volume ratio. Folder rows show local/cloud sizes. The capacity gauge uses the provider quota endpoint; its observation is cached. Fonts are bundled locally as TTF assets derived from the desktop WOFF2 assets.

The app contains no telemetry, WebView, general file-sharing server or remote control. The launcher is exported; the scheduler service requires Android's signature-level `BIND_JOB_SERVICE`.

## Build and verify

Use Python 3.11+, JDK 17 and Android SDK platform 35/build tools 35.0.0. No Gradle/Maven runtime dependencies are needed. The AOSP `platform/prebuilts/sdk` Windows layout is supported. Build tools and signing material are excluded from releases.

Set a private `ABRE_ANDROID_STORE_PASSWORD` of at least 12 characters, then:

```powershell
python android/build.py --jdk C:/Tools/jdk-17 --sdk C:/Tools/android-sdk --keystore C:/Private/abre-release.jks
python android/check.py --jdk C:/Tools/jdk-17 --json-jar C:/Tools/json-20250517.jar
```

Keep and reuse the signing key for updates. Output and SHA-256 checksums are in `dist/android`; intermediates are in `build/android`. The build compiles all native sources/resources, creates DEX, aligns the APK, signs it and verifies signatures/alignment. The test-only JSON-java JAR version is `20250517`.

The 1.7.0 adapter/schedule suite uses queued fake connections and synthetic files: original names, duplicate conflicts, safe destinations, metadata checks, native overwrite, trash with ambiguous-response reconciliation, listing cache, credential isolation, retry journals, byte units and durable daily schedules. These checks do not use a phone or real provider account. Build metadata explicitly records `physicalDeviceTested: false` unless device validation actually occurred.

Bundled fonts can be regenerated with `python packaging/convert_android_fonts.py` after installing `fonttools[woff]` in a build environment. Their separate terms are in [NOTICE](../NOTICE.md).

Official platform references: [directory access](https://developer.android.com/training/data-storage/shared/documents-files), [foreground-service timeouts](https://developer.android.com/develop/background-work/services/fgs/timeout), [JobInfo.Builder](https://developer.android.com/reference/android/app/job/JobInfo.Builder) and [Android Keystore](https://developer.android.com/privacy-and-security/keystore).
