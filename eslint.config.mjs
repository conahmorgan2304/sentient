// Catches a name used in the panel that isn't defined where it's used (a page that won't open), and a method
// defined twice (the second silently replaces the first, as _drawBrain did from 1.5.0 to 1.5.14).
import globals from "globals";

export default [{
  files: ["custom_components/sentient/frontend/*.js"],
  languageOptions: { ecmaVersion: 2023, sourceType: "script", globals: { ...globals.browser } },
  rules: { "no-undef": "error", "no-dupe-class-members": "error", "no-dupe-keys": "error" },
}];
