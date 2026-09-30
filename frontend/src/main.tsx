import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/notifications/styles.css";
import "./styles.css";

import { MantineProvider, createTheme } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { ApiError } from "./api";
import App from "./App";

const theme = createTheme({
  primaryColor: "navy",
  colors: {
    navy: [
      "#eef3fa", "#dbe4f1", "#b3c6e2", "#88a6d3", "#648bc6", "#4d7abf", "#4071bc",
      "#315fa6", "#285495", "#1f3a5f",
    ],
  },
  primaryShade: { light: 7, dark: 5 },
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  defaultRadius: "md",
  headings: { fontWeight: "650" },
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="light">
      <QueryClientProvider client={queryClient}>
        {/* Inside the router, so notices and dialogs can hold links. */}
        <BrowserRouter>
          <ModalsProvider>
            <Notifications position="bottom-right" />
            <App />
          </ModalsProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
