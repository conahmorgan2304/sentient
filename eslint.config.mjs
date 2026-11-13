// Catches a name used in the panel that isn't defined where it's used (a page that won't open).
import globals from "globals";

export default [{
  files: ["custom_components/sentient/frontend/*.js"],
  languageOptions: { ecmaVersion: 2023, sourceType: "script", globals: { ...globals.browser } },
  rules: { "no-undef": "error" },
}];
