# Cold-start de contenedores bajo cargas reales

Este repositorio contiene el avance preliminar del proyecto de Sistemas Operativos Avanzados sobre medición de *cold-start* en contenedores, comparando runtime OCI, aislamiento y uso de GPU.

## Estado actual del avance

En esta etapa preliminar, la traza pública **Azure Functions 2019** fue usada para construir eventos de invocación a partir del archivo:

```text
invocations_per_function_md.anon.d01.csv
```

Para generar los eventos se utilizaron las columnas:

- `HashApp`
- `HashFunction`
- `Trigger`
- minutos `1..1440`

La clasificación de invocaciones **frías/calientes** usa, por ahora, una política simple de keep-alive fijo de 20 minutos por aplicación.

## Alcance actual de las mediciones

Las mediciones Docker incluidas en este avance son **pruebas controlados por configuración**. La integración completa de la traza como generador temporal de ejecuciones queda para la siguiente fase del proyecto.

La métrica:

```text
image_load_ms
```

mide el tiempo de `docker load` desde un archivo `.tar` local.

La prueba GPU actual ejecuta:

```text
cuInit(0)
cuDeviceGetCount
```

Esto permite verificar inicialización del driver CUDA y detección de GPU, pero todavía no crea un contexto CUDA explícito ni carga datos/modelos en VRAM.

## Estructura del repositorio

| Carpeta / archivo | Contenido | Estado en este avance |
|---|---|---|
| `data/` | Archivo de la traza Azure Functions 2019 y el archivo derivado `events_sample.csv`. | Se usa `invocations_per_function_md.anon.d01.csv` para construir eventos preliminares. |
| `docker/app/` | Aplicación mínima usada como carga experimental y su `Dockerfile`. | Permite probar modo CPU y modo GPU controlado. |
| `images/` | Imagen Docker exportada como `.tar`, por ejemplo `coldstart-app-prelim.tar`. | Usada para medir `image_load_ms` con `docker load` local. |
| `scripts/` | Scripts para preparar la traza, ejecutar mediciones y resumir resultados. | Automatiza el pipeline preliminar. |
| `results/` | Salidas experimentales como `raw_coldstart.csv` y `summary_coldstart.csv`. | Contiene las mediciones y percentiles preliminares. |

## Integración con la traza de Azure Functions 2019

Se integró la traza Azure Functions 2019 al ambiente experimental. A partir de las columnas minuto 1..1440 se genera un calendario de eventos cold-start usando una política de keep-alive de 20 minutos. El calendario se reproduce con escala temporal controlada y se ejecuta sobre las configuraciones runc, crun, runsc y GPU, manteniendo constante la imagen y el patrón de llegadas.

<p align="center"><img src="https://github.com/macastro/ColdStart-FactoresRendimiento/blob/main/capturas/Captura_Eventos.png"></p>
<p align="center"><img src="https://github.com/macastro/ColdStart-FactoresRendimiento/blob/main/capturas/Captura_Resultados.png"></p>

## Limitaciones conocidas

La separación entre las etapas **sandbox** y **runtime** todavía es aproximada. Será refinada en la siguiente fase usando eventos de `containerd` y logs de `runsc --debug`.

Las celdas **gVisor + GPU** forman parte del diseño factorial del proyecto, pero su ejecución depende de la compatibilidad de `nvproxy` con el driver NVIDIA y la GPU disponibles en el equipo de pruebas.

## Objetivo de la siguiente fase

Las siguientes actividades previstas son:

Integrar la traza como generador temporal de ejecuciones.
- Refinar la descomposición entre sandbox y runtime.
- Ejecutar más repeticiones por configuración.
- Validar con mayor detalle los casos GPU.
- Evaluar, cuando sea posible, gVisor + GPU mediante nvproxy.
- Generar gráficos comparativos y análisis final de resultados.
