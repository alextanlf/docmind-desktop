import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

// jsdom does not implement scrollIntoView; renderer navigation relies on the browser API.
Element.prototype.scrollIntoView = () => {};
