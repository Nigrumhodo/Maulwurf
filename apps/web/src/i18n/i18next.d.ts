import "i18next";
import type { DEFAULT_NAMESPACE, SourceResources } from "./resources";

// Typed keys: a `t("ns:key")` that is missing from the Spanish source fails `tsc`.
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: typeof DEFAULT_NAMESPACE;
    resources: SourceResources;
    returnNull: false;
  }
}
