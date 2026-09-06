// Minimal ambient declaration for the `jsdom` package (a pre-existing
// devDependency with no bundled types and no `@types/jsdom` installed).
// Deliberately scoped to only the shape `tree-scroll-restore-bootstrap.test.ts`
// actually uses, rather than adding a new dependency for a full type
// package.
declare module "jsdom" {
  export interface DOMWindow extends Window {
    sessionStorage: Storage;
  }

  // Vitest's own bundled types reference `jsdomTypes.ConstructorOptions`
  // (from the real `@types/jsdom` shape) regardless of whether this
  // project imports it -- this ambient module must export a compatible
  // name, even though this file only actually uses a small subset of it.
  export interface ConstructorOptions {
    runScripts?: "dangerously" | "outside-only";
    url?: string;
    beforeParse?(window: DOMWindow): void;
    [key: string]: unknown;
  }

  export class JSDOM {
    constructor(html?: string, options?: ConstructorOptions);
    window: DOMWindow;
  }
}
