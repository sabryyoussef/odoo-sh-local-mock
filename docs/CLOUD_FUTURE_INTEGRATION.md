# Future integration — Developer Platform → catalogues

**Do not implement in this phase.**

Helpers ERP operators may later promote a reviewed Developer Platform build into a versioned package or vertical template:

1. Developer Platform produces an exact-SHA build.
2. Internal technical review (security, module set, upgrade compatibility).
3. Approved internal release artifact.
4. Operator publishes the artifact to **Ready Solutions** (vertical template) or **Helpers ERP Cloud** (generic approved package).
5. End customers consume only the published catalogue item.

Rules:

- Publication is operator-controlled.
- Cloud and Ready Solutions customers never see GitHub, branches, SHAs, or build logs.
- A Developer Platform build is never automatically a customer package.
- A vertical template is never selected as a generic Cloud package unless explicitly published for that purpose.
