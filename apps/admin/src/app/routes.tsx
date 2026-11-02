import type { RouteObject } from "react-router-dom";
import { PublicLayout } from "../auth/PublicLayout";
import { RequireAuth } from "../auth/RequireAuth";
import { AppLayout } from "../components/AppLayout";
import { AddScreen } from "../routes/AddScreen";
import { ContentLayout } from "../routes/ContentLayout";
import { More } from "../routes/More";
import { Redirect } from "./Redirect";
import { Bootstrap } from "../routes/Bootstrap";
import { ChangePassword } from "../routes/ChangePassword";
import { Home } from "../routes/Home";
import { Login } from "../routes/Login";
import { MediaLibrary } from "../routes/MediaLibrary";
import { NotFound } from "../routes/NotFound";
import { Notifications } from "../routes/Notifications";
import { PlaylistEditor } from "../routes/PlaylistEditor";
import { Playlists } from "../routes/Playlists";
import { Power } from "../routes/Power";
import { Schedules } from "../routes/Schedules";
import { Screens } from "../routes/Screens";
import { SiteDetail } from "../routes/SiteDetail";
import { Sites } from "../routes/Sites";

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
          { path: "screens/add", element: <AddScreen /> },
          { path: "screens/add/android", element: <AddScreen android /> },
          // Anciens parcours (création, appairage, Android) fusionnés dans l'assistant ; la requête (?screen=, ?address=) est conservée.
          { path: "screens/android", element: <Redirect to="/screens/add/android" /> },
          {
            path: "content",
            element: <ContentLayout />,
            children: [
              { index: true, element: <Redirect to="/content/media" /> },
              { path: "media", element: <MediaLibrary /> },
              { path: "playlists", element: <Playlists /> },
              { path: "playlists/:id", element: <PlaylistEditor /> },
            ],
          },
          { path: "playback", element: <Schedules /> },
          { path: "more", element: <More /> },
          { path: "sites", element: <Sites /> },
          { path: "sites/:id", element: <SiteDetail /> },
          { path: "power", element: <Power /> },
          { path: "notifications", element: <Notifications /> },
          // Anciennes URL (signets, liens externes) : redirections, base conservée par le routeur.
          { path: "supervision", element: <Redirect to="/screens?filter=issues" /> },
          { path: "pairing", element: <Redirect to="/screens/add" /> },
          { path: "media", element: <Redirect to="/content/media" /> },
          { path: "playlists", element: <Redirect to="/content/playlists" /> },
          { path: "playlists/:id", element: <Redirect to="/content/playlists/:id" /> },
          { path: "schedules", element: <Redirect to="/playback" /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
];
