import { Center, Text } from "@mantine/core";
import { Navigate, Route, Routes } from "react-router-dom";

import Logo from "./components/Logo";
import { AppProvider, useAppInfo } from "./context";
import Shell from "./layout/Shell";
import DetailPage from "./module/DetailPage";
import FormPage from "./module/FormPage";
import ImportPage from "./module/ImportPage";
import ListPage from "./module/ListPage";
import AcceptInvite from "./pages/AcceptInvite";
import ArchivePage from "./pages/Archive";
import ChangePassword from "./pages/ChangePassword";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Preferences from "./pages/Preferences";
import Setup from "./pages/Setup";
import SettingsPage from "./settings/SettingsPage";

export default function App() {
  const { data: info, isLoading, error } = useAppInfo();

  if (isLoading) {
    return (
      <Center h="100vh">
        <div className="df-boot"><Logo size={40} /></div>
      </Center>
    );
  }
  if (error || !info) {
    return (
      <Center h="100vh">
        <Text c="red">Dealflow can't reach its server. Check that it is running and reload.</Text>
      </Center>
    );
  }

  if (info.needs_setup) {
    return (
      <Routes>
        <Route path="*" element={<Setup />} />
      </Routes>
    );
  }

  if (!info.user) {
    return (
      <Routes>
        <Route path="/invite/:token" element={<AcceptInvite />} />
        <Route path="*" element={<Login firmName={info.firm_name} />} />
      </Routes>
    );
  }

  if (info.user.must_change_password) {
    return <ChangePassword forced />;
  }

  return (
    <AppProvider info={info}>
      <Routes>
        <Route path="/invite/:token" element={<Navigate to="/" replace />} />
        <Route element={<Shell />}>
          <Route index element={<Home />} />
          <Route path="m/:module" element={<ListPage />} />
          <Route path="m/:module/new" element={<FormPage />} />
          <Route path="m/:module/import" element={<ImportPage />} />
          <Route path="m/:module/:id" element={<DetailPage />} />
          <Route path="m/:module/:id/edit" element={<FormPage />} />
          <Route path="archive" element={<ArchivePage />} />
          <Route path="settings/*" element={<SettingsPage />} />
          <Route path="preferences" element={<Preferences />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AppProvider>
  );
}
