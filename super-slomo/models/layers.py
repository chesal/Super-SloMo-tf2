import tensorflow as tf
import tensorflow_addons as tfa


class UNet(tf.keras.layers.Layer):
    def __init__(self, out_filters, name="UNet", **kwargs):
        super(UNet, self).__init__(name=name, **kwargs)
        self.out_filters = out_filters

    def build(self, input_shape):
        self.leaky_relu = tf.keras.layers.LeakyReLU(alpha=0.1)
        self.conv1 = tf.keras.layers.Conv2D(
            filters=32, kernel_size=7, strides=1, padding="same"
        )
        self.conv2 = tf.keras.layers.Conv2D(
            filters=32, kernel_size=7, strides=1, padding="same"
        )
        self.encoder1 = Encoder(64, 5)
        self.encoder2 = Encoder(128, 5)
        self.encoder3 = Encoder(256, 3)
        self.encoder4 = Encoder(512, 3)
        self.encoder5 = Encoder(512, 3)
        self.decoder1 = Decoder(512)
        self.decoder2 = Decoder(256)
        self.decoder3 = Decoder(128)
        self.decoder4 = Decoder(64)
        self.decoder5 = Decoder(32)
        self.conv3 = tf.keras.layers.Conv2D(
            filters=self.out_filters, kernel_size=3, strides=1, padding="same"
        )

    def call(self, inputs, **kwargs):
        x_enc = self.conv1(inputs)
        x_enc = self.leaky_relu(x_enc)
        skip = self.conv2(x_enc)
        skip1 = self.leaky_relu(skip)
        skip2 = self.encoder1(skip1)
        skip3 = self.encoder2(skip2)
        skip4 = self.encoder3(skip3)
        skip5 = self.encoder4(skip4)
        x_enc = self.encoder5(skip5)
        x_dec = self.decoder1([x_enc, skip5])
        x_dec = self.decoder2([x_dec, skip4])
        x_dec = self.decoder3([x_dec, skip3])
        x_dec = self.decoder4([x_dec, skip2])
        x_dec = self.decoder5([x_dec, skip1])
        x_dec = self.conv3(x_dec)
        x_dec = self.leaky_relu(x_dec)
        return x_dec


class Encoder(tf.keras.layers.Layer):
    """
    Average Pooling -> CNN + Leaky ReLU -> CNN + Leaky ReLU
    Used to create a UNet like architecture.
    """

    def __init__(self, filters, kernel_size, **kwargs):
        super(Encoder, self).__init__(**kwargs)
        self.filters = filters
        self.kernel_size = kernel_size

    def build(self, input_shape):
        self.conv1 = tf.keras.layers.Conv2D(
            filters=self.filters,
            kernel_size=self.kernel_size,
            strides=1,
            padding="same",
        )
        self.conv2 = tf.keras.layers.Conv2D(
            filters=self.filters,
            kernel_size=self.kernel_size,
            strides=1,
            padding="same",
        )
        self.avg_pool = tf.keras.layers.AveragePooling2D()
        self.leaky_relu = tf.keras.layers.LeakyReLU(alpha=0.1)

    def call(self, inputs, **kwargs):
        x = self.avg_pool(inputs)
        x = self.conv1(x)
        x = self.leaky_relu(x)
        x = self.conv2(x)
        x = self.leaky_relu(x)
        return x


class Decoder(tf.keras.layers.Layer):
    """
    Bilinear interpolation -> CNN + Leaky ReLU -> CNN + Leaky ReLU
    Used to create a UNet like architecture.
    """

    def __init__(self, filters, **kwargs):
        super(Decoder, self).__init__(**kwargs)
        self.filters = filters

    def build(self, input_shape):
        self.conv1 = tf.keras.layers.Conv2D(
            filters=self.filters, kernel_size=3, strides=1, padding="same"
        )
        self.conv2 = tf.keras.layers.Conv2D(
            filters=self.filters, kernel_size=3, strides=1, padding="same"
        )
        self.interpolation = tf.keras.layers.UpSampling2D(interpolation="bilinear")
        self.leaky_relu = tf.keras.layers.LeakyReLU(alpha=0.1)

    def call(self, inputs, **kwargs):
        x, skip = inputs
        x = self.interpolation(x)
        x = self.conv1(x)
        x = self.leaky_relu(x)

        # pad smaller matrix
        x_delta = skip.shape[1] - x.shape[1]
        y_delta = skip.shape[2] - x.shape[2]
        x = tf.pad(
            x, tf.convert_to_tensor([[0, 0], [0, x_delta], [0, y_delta], [0, 0]])
        )

        x = tf.keras.layers.Concatenate(axis=3)([x, skip])
        x = self.conv2(x)
        x = self.leaky_relu(x)
        return x


class BackWarp(tf.keras.layers.Layer):
    """
    Backwarping to an image.
    Generate I_0 <- backwarp(F_0_1, I_1) given optical
    flow from frame I_0 to I_1 -> F_0_1 and frame I_1.
    """

    def __init__(self, **kwargs):
        super(BackWarp, self).__init__(**kwargs)

    def build(self, input_shape):
        self.backwarp = tfa.image.dense_image_warp

    def call(self, inputs, **kwargs):
        image, flow = inputs
        img_backwarp = self.backwarp(image, flow)
        return img_backwarp


class OpticalFlow(tf.keras.layers.Layer):
    def __init__(self, **kwargs):
        super(OpticalFlow, self).__init__(**kwargs)

    def build(self, input_shape):
        self.flow_interp_layer = UNet(5, name="flow_interp")
        self.backwarp_layer_t0 = BackWarp()
        self.backwarp_layer_t1 = BackWarp()

    def call(self, inputs, **kwargs):
        frames_0, frames_1, f_01, f_10, t_indeces = inputs

        t0_value = (-1 * (1 - t_indeces)) * t_indeces
        t1_value = t_indeces * t_indeces
        f_t0_t = (t0_value * f_01) + (t1_value * f_10)

        t1_value = (1 - t_indeces) * (1 - t_indeces)
        f_t1_t = (t1_value * f_01) - (t0_value * f_10)

        # flow interpolation
        g_i0_ft0 = self.backwarp_layer_t0([frames_0, f_t0_t])
        g_i1_ft1 = self.backwarp_layer_t1([frames_1, f_t1_t])

        flow_interp_in = tf.concat(
            [frames_0, frames_1, f_01, f_10, f_t1_t, f_t0_t, g_i1_ft1, g_i0_ft0], axis=3
        )
        flow_interp_out = self.flow_interp_layer(flow_interp_in)

        # optical flow residuals and visibility maps
        delta_f_t0 = flow_interp_out[:, :, :, :2]
        delta_f_t1 = flow_interp_out[:, :, :, 2:4]

        # visibility map
        v_t0 = tf.keras.activations.sigmoid(flow_interp_out[:, :, :, 4:5])
        v_t1 = 1 - v_t0

        f_t0 = f_t0_t + delta_f_t0
        f_t1 = f_t1_t + delta_f_t1

        return f_t0, v_t0, f_t1, v_t1, g_i0_ft0, g_i1_ft1


class Output(tf.keras.layers.Layer):
    def __init__(self, **kwargs):
        super(Output, self).__init__(**kwargs)

    def build(self, input_shape):
        self.backwarp_layer_t0 = BackWarp()
        self.backwarp_layer_t1 = BackWarp()

    def call(self, inputs, **kwargs):
        frames_0, f_t0, v_t0, frames_1, f_t1, v_t1, t_indeces = inputs

        # flow interpolation
        g_i0_ft0 = self.backwarp_layer_t0([frames_0, f_t0])
        g_i1_ft1 = self.backwarp_layer_t1([frames_1, f_t1])

        z = ((1 - t_indeces) * v_t0) + (t_indeces * v_t1) + 1e-12
        frame_pred = ((1 - t_indeces) * v_t0 * g_i0_ft0) + (t_indeces * v_t1 * g_i1_ft1)
        frame_pred = tf.divide(frame_pred, z)
        return frame_pred
