import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/notifications/styles.css";
import "./styles.css";

import { MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { ApiError } from "./api";
import App from "./App";
import { cssVariables, theme } from "./theme";



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
    <MantineProvider theme={theme} cssVariablesResolver={cssVariables} defaultColorScheme="light" forceColorScheme="light">
      <QueryClientProvider client={queryClient}>
        {/* Inside the router, so notices and dialogs can hold links. */}
        <BrowserRouter>
          <ModalsProvider>
            {/* Below dialogs (z 200), so a notice never covers a dialog's buttons. */}
            <Notifications position="bottom-right" limit={3} zIndex={190} transitionDuration={220} />
            <App />
          </ModalsProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </React.StrictMode>,
);
