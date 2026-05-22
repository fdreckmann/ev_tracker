// settings.js — Settings tab permission helper functions
// Provides: _applySettingsPermissions

function _applySettingsPermissions() {
  if (!hasPermission('settings:edit')) {
    // Settings-Speichern-Buttons deaktivieren
    document.querySelectorAll('[onclick*="saveSettings"], [onclick*="saveCfg"]').forEach(btn => {
      btn.disabled = true;
      btn.title = 'Keine Berechtigung: settings:edit';
      btn.style.opacity = '0.5';
    });
  }
}
