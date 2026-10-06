import BaseIcon from "./BaseIcon.js";

export default {
  name: "ErrorNote",
  components: { BaseIcon },
  props: { message: String },
  template: `
    <p v-if="message" class="alert alert--error" role="alert">
      <BaseIcon name="alert" :size="18" />
      <span>{{ message }}</span>
    </p>
  `,
};
