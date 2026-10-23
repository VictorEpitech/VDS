import type { RouteObject } from "react-router-dom";
import { PublicLayout } from "../auth/PublicLayout";
import { RequireAuth } from "../auth/RequireAuth";
import { AppLayout } from "../components/AppLayout";
import { Bootstrap } from "../routes/Bootstrap";
import { ChangePassword } from "../routes/ChangePassword";
import { Home } from "../routes/Home";
import { Login } from "../routes/Login";
import { MediaLibrary } from "../routes/MediaLibrary";
import { NotFound } from "../routes/NotFound";
import { Pairing } from "../routes/Pairing";
import { Notifications } from "../routes/Notifications";
import { PlaylistEditor } from "../routes/PlaylistEditor";
import { Playlists } from "../routes/Playlists";
import { Power } from "../routes/Power";
import { Schedules } from "../routes/Schedules";
import { Screens } from "../routes/Screens";
import { SiteDetail } from "../routes/SiteDetail";
import { Sites } from "../routes/Sites";
import { Supervision } from "../routes/Supervision";

export const routes: RouteObject[] = [
  {
    element: <PublicLayout />,
    children: [
      { path: "login", element: <Login /> },
      { path: "bootstrap", element: <Bootstrap /> },
      { path: "change-password", element: <ChangePassword /> },
    ],
  },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Home /> },
          { path: "screens", element: <Screens /> },
          { path: "sites", element: <Sites /> },
          { path: "sites/:id", element: <SiteDetail /> },
          { path: "supervision", element: <Supervision /> },
          { path: "power", element: <Power /> },
          { path: "notifications", element: <Notifications /> },
          { path: "pairing", element: <Pairing /> },
          { path: "media", element: <MediaLibrary /> },
          { path: "playlists", element: <Playlists /> },
          { path: "playlists/:id", element: <PlaylistEditor /> },
          { path: "schedules", element: <Schedules /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];
