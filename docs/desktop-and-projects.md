# Desktop launcher and project folders

From a Windows source checkout, create shortcuts with:

```powershell
$env:PYTHONPATH = 'src'
python -m hamrahi_backup.shortcuts --data-dir C:\AbrHamrahiAPIState --port 8765
```

Use the **same private state directory and port** as the running application.
The command creates **Abre Hamrahi API** and **Abre Hamrahi Cloud** on the current
user's desktop. The application shortcut checks local readiness, starts the
background process if needed, and opens the browser only when the panel is ready.
Repeated clicks reuse the listener; a separate launch lock serializes cold starts.
The shortcut does not register Windows startup or change backup configuration.
It uses the Windows Shell API without changing PowerShell execution policy.
Installed packages also provide `abre-hamrahi-shortcuts` with the same options.

For an installed package, `abre-hamrahi-open` provides the same behavior with
`--data-dir` and `--port`. If readiness times out or a conflicting port/state lock
prevents launch, it reports failure instead of opening an unready page. Keep the
checkout at its original path or recreate its shortcuts after moving it.

## Before adding a project parent directory

1. Preserve existing source entries and their IDs. Add the parent as a separate
   source only when it does not overlap an already selected source.
2. Pause automation while reviewing a new broad source and its exclusions.
3. Exclude credential stores, session/profile directories, local runtime state,
   dependency trees, generated builds, test artifacts, and backup output. Ordinary
   source files can also contain embedded secrets; filename exclusions alone do
   not detect these. Review configuration and source locally before uploading.
4. Run preview; inspect eligible file count, byte total and errors. Excluded files
   are intentionally omitted from the backup and cannot be restored from it.
5. Enable automatic sync and file-change sync, save, and resume. A first backup
   can take considerable time when many small files require separate transfers.

The longer of debounce and settle time defines the minimum quiet delay. Uploads
and SHA-256 verification add network time. This is near-real-time backup, not a
zero-delay mirrored filesystem. After a successful initial pass, ordinary file
edits are batched into partial runs that read only their affected paths. A change
arriving during a run remains queued. Renames, deletions, directory events, more
than 1,000 queued paths, configuration changes, restart and periodic integrity
checks use full reconciliation. Partial manifests never replace the full index
or delete unrelated cloud copies. Keep the full manifests as well as partial ones.

New installations exclude common private directories and key/session file types.
Existing exclusion lists are preserved on upgrade; merge additional exclusions
deliberately. Application private state is always excluded. A transient monitor
exception is reported without its private exception detail, then retried while
the scheduler remains alive. Persistent errors stay visible in Processing.
