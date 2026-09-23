# Webui

This project was generated using [Angular CLI](https://github.com/angular/angular-cli) version 20.3.37.

## Development server

To start a local development server, run:

```bash
ng serve
```

Once the server is running, open your browser and navigate to `http://localhost:4200/`. The application will automatically reload whenever you modify any of the source files.

## Code scaffolding

Angular CLI includes powerful code scaffolding tools. To generate a new component, run:

```bash
ng generate component component-name
```

For a complete list of available schematics (such as `components`, `directives`, or `pipes`), run:

```bash
ng generate --help
```

## Building

To build the project run:

```bash
ng build
```

This will compile your project and store the build artifacts in the `dist/` directory. By default, the production build optimizes your application for performance and speed.

## Security headers

`src/index.html` carries the Content-Security-Policy as a `<meta>` tag. Its
`connect-src` names the Lambda Function URL from `environment.prod.ts`, so
update both together if the API moves.

The production build sets `inlineCritical: false` in `angular.json`. With it
on, the build loads the stylesheet through an inline `onload` handler, which
`script-src 'self'` blocks, and the global styles never apply.

Some headers only work as real HTTP headers, not `<meta>`. Add them with a
CloudFront response headers policy on the S3 behaviour:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `frame-ancestors 'none'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |

A header CSP and a `<meta>` CSP both apply, so the header one only needs
`frame-ancestors`.

## Running unit tests

To execute unit tests with the [Karma](https://karma-runner.github.io) test runner, use the following command:

```bash
ng test
```

## Running end-to-end tests

For end-to-end (e2e) testing, run:

```bash
ng e2e
```

Angular CLI does not come with an end-to-end testing framework by default. You can choose one that suits your needs.

## Additional Resources

For more information on using the Angular CLI, including detailed command references, visit the [Angular CLI Overview and Command Reference](https://angular.dev/tools/cli) page.
