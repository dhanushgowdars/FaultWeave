import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import App from "./App";

describe("FaultWeave command center", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it("starts behind the genuine engineer authentication gate", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /open the command center/i })).toBeInTheDocument();
    expect(screen.getByText(/only the engineer role can control demonstrations/i)).toBeInTheDocument();
    expect(screen.getByText(/six real backend cases/i)).toBeInTheDocument();
  });
});
