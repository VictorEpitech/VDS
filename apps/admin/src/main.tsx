import { bootstrap } from "./app/bootstrap";
import "./styles/app.css";

const root = document.getElementById("root");
if (root) void bootstrap(root);
