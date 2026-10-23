import type { RouteObject } from "react-router-dom";
import { AppLayout } from "../components/AppLayout";
import { Home } from "../routes/Home";
import { NotFound } from "../routes/NotFound";
import { Screens } from "../routes/Screens";

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <Home /> },
      { path: "screens", element: <Screens /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
