// Assets visuales (proporcionados en src/components/Visual/), empaquetados como WebP sin
// pérdida: el PNG original sumaba 6,6 MiB y dominaba la primera carga. Todos los nombres
// van en minúsculas porque el build de Docker (Linux) distingue mayúsculas.
// Variantes por tema: `folder`/`madriguera` son la versión oscura; `*-claro` la clara.
import aLaEspera from "./a-la-espera.webp";
import chat1 from "./chat1.webp";
import chat2 from "./chat2.webp";
import folder from "./folder.webp";
import folderClaro from "./folder-claro.webp";
import loginup from "./loginup.webp";
import loginuposcuro from "./loginuposcuro.webp";
import madriguera from "./madriguera.webp";
import madrigueraClaro from "./madriguera-claro.webp";

export {
  aLaEspera,
  chat1,
  chat2,
  folder,
  folderClaro,
  loginup,
  loginuposcuro,
  madriguera,
  madrigueraClaro,
};
