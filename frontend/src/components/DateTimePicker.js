import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import BaseIcon from "./BaseIcon.js";

/* Date and time field with a calendar and a time list drawn in the page.
   The browser's own picker is a separate window that looks different on
   every platform. v-model is "YYYY-MM-DDTHH:MM" in local time, the same
   value an <input type="datetime-local"> gives. */
const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

// Bookable start times: every 30 minutes from 11:00 to 21:30.
const TIMES = [];
for (let h = 11; h <= 21; h++) for (const m of [0, 30]) TIMES.push(`${pad(h)}:${pad(m)}`);
const timeLabel = (t) => {
  const [h, m] = t.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
};

export default {
  name: "DateTimePicker",
  components: { BaseIcon },
  props: {
    modelValue: { type: String, required: true },
    id: { type: String, default: undefined },
  },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const open = ref(false);
    const root = ref(null);
    const timeList = ref(null);
    const day = ref("");             // day picked in the open calendar
    const month = ref(new Date());   // first day of the month on show
    const today = isoDay(new Date());

    const current = computed(() => {
      const [d, t = ""] = props.modelValue.split("T");
      return { day: d, time: t.slice(0, 5) };
    });
    const label = computed(() => {
      const dt = new Date(props.modelValue);
      return Number.isNaN(dt.getTime())
        ? "Choose a date"
        : dt.toLocaleString(undefined, {
            weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
          });
    });
    const monthLabel = computed(() =>
      month.value.toLocaleDateString(undefined, { month: "long", year: "numeric" })
    );
    const cells = computed(() => {
      const y = month.value.getFullYear();
      const m = month.value.getMonth();
      const out = Array(new Date(y, m, 1).getDay()).fill(null);
      const count = new Date(y, m + 1, 0).getDate();
      for (let d = 1; d <= count; d++) {
        const value = isoDay(new Date(y, m, d));
        out.push({ day: d, value, disabled: value < today });
      }
      return out;
    });
    const canGoBack = computed(() => {
      const lastOfPrev = new Date(month.value.getFullYear(), month.value.getMonth(), 0);
      return isoDay(lastOfPrev) >= today;
    });
    // Times already past today cannot be booked.
    const times = computed(() => {
      const now = new Date();
      const nowTime = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
      return TIMES.map((t) => ({
        value: t, label: timeLabel(t), disabled: day.value === today && t <= nowTime,
      }));
    });

    async function toggle() {
      if (open.value) {
        open.value = false;
        return;
      }
      day.value = current.value.day;
      const d = new Date(`${current.value.day}T00:00`);
      month.value = new Date(d.getFullYear(), d.getMonth(), 1);
      open.value = true;
      // Show the selected time without scrolling the page.
      await nextTick();
      const list = timeList.value;
      const sel = list?.querySelector(".is-selected");
      if (list && sel) list.scrollTop = sel.offsetTop - list.clientHeight / 2 + sel.offsetHeight / 2;
    }
    function shift(n) {
      month.value = new Date(month.value.getFullYear(), month.value.getMonth() + n, 1);
    }
    function pickDay(c) {
      if (!c.disabled) day.value = c.value;
    }
    function pickTime(t) {
      if (t.disabled) return;
      emit("update:modelValue", `${day.value}T${t.value}`);
      open.value = false;
    }

    // Close on a click outside or on Escape.
    const onDown = (e) => { if (open.value && !root.value?.contains(e.target)) open.value = false; };
    const onKey = (e) => { if (e.key === "Escape") open.value = false; };
    onMounted(() => {
      document.addEventListener("mousedown", onDown);
      document.addEventListener("keydown", onKey);
    });
    onBeforeUnmount(() => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    });

    return {
      open, root, timeList, day, current, label, monthLabel, cells, canGoBack, times,
      toggle, shift, pickDay, pickTime, WEEKDAYS,
    };
  },
  template: `
    <div ref="root" class="dtpick">
      <button :id="id" type="button" class="dtpick__field" aria-haspopup="dialog"
              :aria-expanded="open ? 'true' : 'false'" @click="toggle">
        <span>{{ label }}</span>
        <BaseIcon name="calendar" :size="16" />
      </button>

      <div v-if="open" class="dtpick__pop" role="dialog" aria-label="Choose a date and time">
        <div class="dtpick__cal">
          <div class="dtpick__head">
            <button type="button" class="dtpick__nav" :disabled="!canGoBack"
                    aria-label="Previous month" @click="shift(-1)">
              <BaseIcon name="arrowLeft" :size="15" />
            </button>
            <span class="dtpick__month">{{ monthLabel }}</span>
            <button type="button" class="dtpick__nav" aria-label="Next month" @click="shift(1)">
              <BaseIcon name="arrowRight" :size="15" />
            </button>
          </div>
          <div class="dtpick__grid">
            <span v-for="w in WEEKDAYS" :key="w" class="dtpick__wd">{{ w }}</span>
            <template v-for="(c, i) in cells" :key="i">
              <span v-if="!c" />
              <button v-else type="button" class="dtpick__day" :disabled="c.disabled"
                      :class="{ 'is-selected': c.value === day, 'is-today': c.value === today }"
                      :aria-pressed="c.value === day ? 'true' : 'false'" @click="pickDay(c)">
                {{ c.day }}
              </button>
            </template>
          </div>
        </div>

        <ul ref="timeList" class="dtpick__times" aria-label="Time">
          <li v-for="t in times" :key="t.value">
            <button type="button" class="dtpick__time" :disabled="t.disabled"
                    :class="{ 'is-selected': t.value === current.time }" @click="pickTime(t)">
              {{ t.label }}
            </button>
          </li>
        </ul>
      </div>
    </div>
  `,
};
