# Android application

`AbreHamrahiAPI-1.5.0-android.apk` is a native Android 8.0+ application. It connects directly to Abrehamrahi over HTTPS. It does not connect to the Windows panel, open a listener, expose the PC to the network, or contain the developer's account. Sign in separately on the phone. This first Android build has compilation, signature, policy and simulated cloud-adapter checks; physical-device login, folder permissions, background scheduling and real provider transfers still need device validation.

## Use

1. Install the APK and open **Abre Hamrahi API**. Account accepts your own SMS code or password; captcha is entered manually if the provider requests it. Passwords and codes are not saved. The session is encrypted with a key in Android Keystore, stored in app-private storage, and excluded from Android backups.
2. In Folders, select each source through Android's system folder chooser. Only read access is requested. Grant persistent access when offered. Selected names and file names remain unchanged. Selecting two roots with the same display name is rejected to prevent mixing their contents.
3. In Settings, review the remote root, size limit and network policy. The default root uses the phone model. The root becomes fixed after the first remote folder is created, so the existing index cannot accidentally be reused for a different location. The default network policy is unmetered connections, usually Wi-Fi. Limits are 50 MiB per file by default, configurable up to 100 MiB, 5,000 files and 32 directory levels per pass.
4. Run Local preview first. It reads and hashes the selected files without contacting the provider. Then run Backup / Retry, or Start monitoring. Reports display individual failure reasons; retry skips content already confirmed in this installation's private index.
5. Start monitoring creates a foreground notification and checks selected document trees approximately every 30 seconds after each pass. Closing the screen leaves the foreground service running. The notification opens the application and offers Stop. Scheduled background work uses Android JobScheduler with a 15-minute minimum interval, permitted network policy, and persistence across reboot. Stop cancels both monitoring and the periodic job.

Android's Storage Access Framework grants access to a document tree, not arbitrary filesystem paths. Android 11+ restricts selection of storage roots, the Downloads root, `Android/data` and `Android/obb`. App-private data belonging to other applications is not accessible. A phone selection refers to the phone's files, not a Windows `D:` folder.

The app does not claim continuous instantaneous sync on Android. Android schedules jobs according to battery, Doze, network and device policy. Android 15's `dataSync` foreground service budget can stop monitoring after six hours in a 24-hour period; the timeout is handled cooperatively and periodic scheduling remains enabled. Open the app and start monitoring again when permitted. Force-stopping the application prevents background work until it is opened again. No request to disable Android security protections is needed.

## Versions and recovery

Each upload is verified by downloading and checking its SHA-256 before recording success. Two local reads must agree before a source snapshot can be uploaded. The app never deletes local or remote files and never sends an overwrite request. A verified predecessor moves, with its original name, into `<phone-root>/_abre-android-data/history/<object-id>/` before the replacement is uploaded under its original name. A stopped transfer can leave the visible location temporarily empty; the predecessor remains in history.

Open the cloud from the processing page to download the current file or a historical version. Restoration is manual. Old versions are not automatically pruned. Remote content is not end-to-end encrypted; the provider can read uploaded bytes. The history is a preservation mechanism, not a guarantee of a provider recycle-bin API.

Successful content is cached locally; this application is a one-way content backup, not a mirror of later cloud edits. It does not delete cloud objects when a local file disappears. An existing same-name cloud file not owned by this installation's index is a conflict; it is not silently adopted or replaced. Clearing app data removes the private index and therefore can require choosing a new remote root or manual review.

A completion journal is persisted before a final upload request. On retry, a matching key and downloaded hash reconcile an uncertain completion without uploading again. If a final response was lost and the cloud does not yet expose the object, that file stays held for manual review rather than guessing and duplicating it. Ordinary pre-completion transfer failures can retry on the next pass. A newer local edit does not prevent recovery of an already completed older transfer; it is first verified, then archived before the newer bytes are sent.

Default exclusions include `.git`, dependency/build/cache directories, `.qa`, `.backup`, environment files, session files, key/certificate files, temporary files and logs. Check the chosen folders before first upload. The UI has no telemetry, WebView, remote control, general file-sharing API or embedded web server. The only exported application activity is the launcher; the scheduler service requires Android's signature-level `BIND_JOB_SERVICE` permission.

## Rebuild and verify

Use JDK 17, Python 3.11+ and the official Android SDK platform 35 and build tools 35.0.0. There are no Gradle, Maven or third-party Android runtime dependencies. AOSP's official `platform/prebuilts/sdk` layout is also supported by the build script on Windows. The build used AOSP commit `3af7c93524be6f51e092b87b17f009f13ee98b43`; source and tool licenses are in the respective upstream distributions. Tools and private signing material are not part of the APK or repository.

Set `ABRE_ANDROID_STORE_PASSWORD` to a private signing password of at least 12 characters, then:

```powershell
python android/build.py --jdk C:/Tools/jdk-17 --sdk C:/Tools/android-sdk --keystore C:/Private/abre-release.jks
```

Keep the keystore and its password private and backed up separately. The script generates a key only when the explicitly selected path does not exist. Reuse it for updates; a different key cannot update an existing install. Signing keys, passwords, sessions and developer paths are never included in published artifacts. The APK is signed as the community project, not as the cloud provider or Google Play. Output, checksums and build metadata stay in `dist/android`; intermediates stay in `build/android`. The script compiles resources and Java, converts bytecode with D8, aligns the APK, signs it, and verifies the signature and alignment.

Adapter checks use the JSON-java `org.json:json:20250517` JAR only as a test dependency, with JDK 17:

```powershell
python android/check.py --jdk C:/Tools/jdk-17 --json-jar C:/Tools/json-20250517.jar
```

The checks use queued fake HTTP connections and synthetic files: original names, duplicate conflicts, folder creation with ambiguous responses, multipart bytes, recovery-journal timing, disabled overwrite, signed-host credential isolation, SHA-256 rejection, token refresh and ambiguous moves. They do not use an account, phone, emulator or provider network. A successful build is not a substitute for the device validation described above.

Official behavior references: Android [directory access](https://developer.android.com/training/data-storage/shared/documents-files), [foreground-service timeouts](https://developer.android.com/develop/background-work/services/fgs/timeout), [JobInfo.Builder](https://developer.android.com/reference/android/app/job/JobInfo.Builder) and [Android Keystore](https://developer.android.com/privacy-and-security/keystore).
